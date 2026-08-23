from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any, *, omit: tuple[str, ...] = ()) -> bytes:
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key not in omit}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any, *, omit: tuple[str, ...] = ()) -> str:
    return hashlib.sha256(canonical_bytes(value, omit=omit)).hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must contain a timezone")
    return parsed


class ContractError(ValueError):
    pass


def verify_content_digest(document: dict, field: str = "content_sha256") -> None:
    expected = document.get(field)
    actual = digest(document, omit=(field,))
    if expected != actual:
        raise ContractError(f"{field} mismatch: expected {expected}, calculated {actual}")


def verify_experiment_bundle(bundle: dict) -> None:
    """Validate invariants JSON Schema cannot express across artifacts."""
    required = (
        "prediction", "hypothesis", "intervention", "measurement_sets",
        "environments", "environment_policy", "statistical_policy",
        "integrity_inputs", "correctness_reports",
    )
    missing = [name for name in required if name not in bundle]
    if missing:
        raise ContractError(f"bundle missing: {', '.join(missing)}")

    prediction = bundle["prediction"]
    hypothesis = bundle["hypothesis"]
    intervention = bundle["intervention"]
    api_policy = bundle["environment_policy"]["api_level"]
    if api_policy["minimum"] > api_policy["maximum"]:
        raise ContractError("environment API policy range is inverted")
    source_manifests = bundle["integrity_inputs"].get("source_manifests", [])
    correctness_reports = bundle["correctness_reports"]
    evidence = [item for items in bundle.get("evidence_by_arm", {}).values() for item in items]
    verify_content_digest(bundle["integrity_inputs"])
    if bundle["integrity_inputs"].get("run_id") != bundle["run_id"]:
        raise ContractError("integrity input belongs to another run")
    for document in (
        prediction, hypothesis, intervention, bundle["statistical_policy"],
        *bundle["measurement_sets"], *bundle["environments"], bundle["environment_policy"],
        *source_manifests, *correctness_reports, *evidence,
    ):
        verify_content_digest(document)

    evidence_ids = [item["id"] for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ContractError("duplicate evidence ID")
    unknown_evidence_arms = set(bundle.get("evidence_by_arm", {})) - {"A1", "B", "A2", "REPLICATION"}
    if unknown_evidence_arms:
        raise ContractError(f"unknown evidence arms: {sorted(unknown_evidence_arms)}")

    manifest_roles: dict[str, dict] = {}
    manifest_ids: set[str] = set()
    for manifest in source_manifests:
        role = manifest["role"]
        if role in manifest_roles:
            raise ContractError(f"duplicate source manifest role: {role}")
        manifest_roles[role] = manifest
        manifest_ids.add(manifest["id"])
        if manifest["run_id"] != bundle["run_id"]:
            raise ContractError("source manifest belongs to another run")
        paths = [entry["path"] for entry in manifest["entries"]]
        if len(paths) != len(set(paths)):
            raise ContractError(f"duplicate source path in {role} manifest")
        if paths != sorted(paths):
            raise ContractError(f"source manifest entries are not canonical in {role}")
        if manifest["tree_sha256"] != digest(manifest["entries"]):
            raise ContractError(f"source tree digest mismatch in {role}")

    report_phases: set[str] = set()
    for report in correctness_reports:
        phase = report["phase"]
        if phase in report_phases:
            raise ContractError(f"duplicate correctness report phase: {phase}")
        report_phases.add(phase)
        if report["run_id"] != bundle["run_id"]:
            raise ContractError("correctness report belongs to another run")
        if report["source_manifest_id"] not in manifest_ids:
            raise ContractError("correctness report references an unknown source manifest")
        if report["failure_count"] + report["skipped_count"] > report["test_count"]:
            raise ContractError("correctness result counts are inconsistent")
        if parse_time(report["started_at"]) > parse_time(report["completed_at"]):
            raise ContractError("correctness report completes before it starts")

    expected_report_sources = {"BASELINE": "BASELINE", "TREATMENT": "TREATMENT"}
    for report in correctness_reports:
        expected = manifest_roles.get(expected_report_sources[report["phase"]])
        if expected and report["source_manifest_id"] != expected["id"]:
            raise ContractError("correctness report is bound to the wrong source role")

    if prediction["hypothesis_id"] != hypothesis["id"]:
        raise ContractError("prediction does not reference bundle hypothesis")
    if prediction["id"] not in hypothesis["prediction_ids"]:
        raise ContractError("hypothesis does not list prediction")
    if intervention["hypothesis_id"] != hypothesis["id"] or intervention["prediction_id"] != prediction["id"]:
        raise ContractError("intervention references the wrong hypothesis or prediction")

    statistical_policy = bundle["statistical_policy"]
    if statistical_policy["prediction_id"] != prediction["id"]:
        raise ContractError("statistical policy references the wrong prediction")
    if statistical_policy["design"] != "a1_b_a2":
        raise ContractError("unsupported preregistered statistical design")

    sets = {(item["metric"], item["arm"]): item for item in bundle["measurement_sets"]}
    if len(sets) != len(bundle["measurement_sets"]):
        raise ContractError("duplicate metric and measurement arm")
    primary_arms = {arm: sets.get((prediction["primary_metric"], arm)) for arm in ("A1", "B", "A2")}
    if any(item is None for item in primary_arms.values()):
        raise ContractError("primary metric A1, B, and A2 measurement sets are required")
    partitions = {item["partition"] for item in bundle["measurement_sets"]}
    if len(partitions) != 1:
        raise ContractError("measurement partitions cannot be mixed")
    if bundle.get("partition") and partitions != {bundle["partition"]}:
        raise ContractError("bundle and measurement partition mismatch")
    first_b = min(parse_time(item["measured_at"]) for item in primary_arms["B"]["measurements"])
    if parse_time(prediction["registered_at"]) >= first_b:
        raise ContractError("prediction was not preregistered before treatment")
    if parse_time(statistical_policy["registered_at"]) >= first_b:
        raise ContractError("statistical policy was not preregistered before treatment")

    environment_ids = {item["id"] for item in bundle["environments"]}
    measurement_ids: set[str] = set()
    metric_units: dict[str, str] = {}
    for measurement_set in bundle["measurement_sets"]:
        if measurement_set["run_id"] != bundle["run_id"]:
            raise ContractError("measurement set belongs to another run")
        sequences = [item["sequence"] for item in measurement_set["measurements"]]
        if len(sequences) != len(set(sequences)):
            raise ContractError(f"duplicate sequence in {measurement_set['arm']}")
        policy = measurement_set["policy"]
        excluded = [item for item in measurement_set["measurements"] if not item["included"]]
        if policy["minimum_included"] != statistical_policy["minimum_included_per_arm"]:
            raise ContractError("measurement minimum does not match statistical policy")
        if policy["max_invalid_percent"] != statistical_policy["max_invalid_percent"]:
            raise ContractError("measurement invalid limit does not match statistical policy")
        allowed = set(policy["predeclared_exclusion_codes"])
        if any(item.get("exclusion_reason") not in allowed for item in excluded):
            raise ContractError(f"unregistered exclusion reason in {measurement_set['arm']}")
        if any(item["environment_snapshot_id"] not in environment_ids for item in measurement_set["measurements"]):
            raise ContractError(f"unknown environment snapshot in {measurement_set['arm']}")

        unit = metric_units.setdefault(measurement_set["metric"], measurement_set["unit"])
        if unit != measurement_set["unit"]:
            raise ContractError(f"metric unit changed: {measurement_set['metric']}")
        for measurement in measurement_set["measurements"]:
            if measurement["id"] in measurement_ids:
                raise ContractError(f"duplicate measurement ID: {measurement['id']}")
            measurement_ids.add(measurement["id"])

        expected_role = {"A1": "BASELINE", "B": "TREATMENT", "A2": "RESTORED"}.get(measurement_set["arm"])
        expected_manifest = manifest_roles.get(expected_role)
        if expected_manifest and any(
            item["source_sha256"] != expected_manifest["tree_sha256"]
            for item in measurement_set["measurements"]
        ):
            raise ContractError(f"measurement source does not match {expected_role} manifest")

    if intervention.get("additional_factors") and not intervention.get("multi_factor_justification"):
        raise ContractError("multi-factor intervention lacks justification")


def verify_partition_registry(registry: dict) -> None:
    verify_content_digest(registry)
    seen: dict[str, str] = {}
    for entry in registry["entries"]:
        artifact_digest = entry["artifact_sha256"]
        partition = entry["partition"]
        previous = seen.get(artifact_digest)
        if previous is not None:
            if previous != partition:
                raise ContractError(f"artifact digest reused across partitions: {artifact_digest}")
            raise ContractError(f"duplicate partition registry entry: {artifact_digest}")
        seen[artifact_digest] = partition


def verify_tool_approval(tool_call: dict, approvals: list[dict]) -> None:
    approval_by_id = {item["id"]: item for item in approvals}
    decision = tool_call["policy_decision"]
    if decision["status"] == "DENY" and tool_call["status"] not in {"DENIED", "REQUESTED"}:
        raise ContractError("denied tool call was executed")
    if decision["status"] == "REQUIRE_APPROVAL":
        if tool_call["status"] != "APPROVAL_PENDING":
            raise ContractError("approval-pending tool call was executed")
        return
    if decision["status"] == "DENY" or tool_call["risk"] not in {"R2", "R3", "R4"}:
        return
    approval_id = decision.get("approval_id")
    approval = approval_by_id.get(approval_id)
    if approval is None:
        raise ContractError("approved high-risk tool call lacks its approval record")
    verify_content_digest(approval)
    if approval["run_id"] != tool_call["run_id"] or approval["risk"] != tool_call["risk"]:
        raise ContractError("approval run or risk does not match tool call")
    if approval["request_sha256"] != tool_call["request_sha256"]:
        raise ContractError("approval does not bind exact tool request")
    if approval["decision"] != "APPROVED":
        raise ContractError("tool call lacks an active approval")
    if parse_time(approval["decided_at"]) > parse_time(tool_call.get("started_at", tool_call["requested_at"])):
        raise ContractError("approval was recorded after execution started")
    if approval.get("expires_at") and parse_time(approval["expires_at"]) <= parse_time(tool_call.get("started_at", tool_call["requested_at"])):
        raise ContractError("approval expired before execution")


def verify_result_bundle(bundle: dict) -> None:
    """Verify final-result references, partitions, rollback, and ledger binding."""
    result = bundle["result"]
    verify_content_digest(result)
    partition = result["partition"]
    run_id = result["run_id"]
    collections = ("artifacts", "measurement_sets", "build_results", "rollback_results")
    for name in collections:
        for document in bundle.get(name, []):
            if document.get("content_sha256"):
                verify_content_digest(document)
            if document.get("run_id") != run_id:
                raise ContractError(f"{name} contains another run")
            if "partition" in document and document["partition"] != partition:
                raise ContractError(f"{name} partition mismatch")
    ids = {
        "artifact_ids": {item["id"] for item in bundle.get("artifacts", [])},
        "measurement_set_ids": {item["id"] for item in bundle.get("measurement_sets", [])},
        "gate_result_ids": {item["gate_id"] for item in bundle.get("gate_results", [])},
    }
    for field, available in ids.items():
        missing = set(result[field]) - available
        if missing:
            raise ContractError(f"dangling {field}: {sorted(missing)}")
    if result["ledger_head_sha256"] != bundle.get("ledger_head_sha256"):
        raise ContractError("result is not bound to supplied ledger head")
    rollback_id = result.get("rollback_result_id")
    if rollback_id and rollback_id not in {item["id"] for item in bundle.get("rollback_results", [])}:
        raise ContractError("dangling rollback result")


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
