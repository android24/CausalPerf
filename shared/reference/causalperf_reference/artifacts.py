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
    required = ("prediction", "hypothesis", "intervention", "measurement_sets", "environments")
    missing = [name for name in required if name not in bundle]
    if missing:
        raise ContractError(f"bundle missing: {', '.join(missing)}")

    prediction = bundle["prediction"]
    hypothesis = bundle["hypothesis"]
    intervention = bundle["intervention"]
    for document in (prediction, hypothesis, intervention, *bundle["measurement_sets"], *bundle["environments"]):
        verify_content_digest(document)

    if prediction["hypothesis_id"] != hypothesis["id"]:
        raise ContractError("prediction does not reference bundle hypothesis")
    if prediction["id"] not in hypothesis["prediction_ids"]:
        raise ContractError("hypothesis does not list prediction")
    if intervention["hypothesis_id"] != hypothesis["id"] or intervention["prediction_id"] != prediction["id"]:
        raise ContractError("intervention references the wrong hypothesis or prediction")

    arms = {item["arm"]: item for item in bundle["measurement_sets"]}
    if not {"A1", "B", "A2"}.issubset(arms):
        raise ContractError("A1, B, and A2 measurement sets are required")
    if len(arms) != len(bundle["measurement_sets"]):
        raise ContractError("duplicate measurement arm")
    partitions = {item["partition"] for item in bundle["measurement_sets"]}
    if len(partitions) != 1:
        raise ContractError("measurement partitions cannot be mixed")
    if bundle.get("partition") and partitions != {bundle["partition"]}:
        raise ContractError("bundle and measurement partition mismatch")
    first_b = min(parse_time(item["measured_at"]) for item in arms["B"]["measurements"])
    if parse_time(prediction["registered_at"]) >= first_b:
        raise ContractError("prediction was not preregistered before treatment")

    environment_ids = {item["id"] for item in bundle["environments"]}
    for measurement_set in bundle["measurement_sets"]:
        if measurement_set["run_id"] != bundle["run_id"]:
            raise ContractError("measurement set belongs to another run")
        sequences = [item["sequence"] for item in measurement_set["measurements"]]
        if len(sequences) != len(set(sequences)):
            raise ContractError(f"duplicate sequence in {measurement_set['arm']}")
        policy = measurement_set["policy"]
        included = [item for item in measurement_set["measurements"] if item["included"]]
        excluded = [item for item in measurement_set["measurements"] if not item["included"]]
        if len(included) < policy["minimum_included"]:
            raise ContractError(f"insufficient included measurements in {measurement_set['arm']}")
        if 100 * len(excluded) / len(measurement_set["measurements"]) > policy["max_invalid_percent"]:
            raise ContractError(f"too many excluded measurements in {measurement_set['arm']}")
        allowed = set(policy["predeclared_exclusion_codes"])
        if any(item.get("exclusion_reason") not in allowed for item in excluded):
            raise ContractError(f"unregistered exclusion reason in {measurement_set['arm']}")
        if any(item["environment_snapshot_id"] not in environment_ids for item in measurement_set["measurements"]):
            raise ContractError(f"unknown environment snapshot in {measurement_set['arm']}")

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
    if decision["status"] != "REQUIRE_APPROVAL":
        return
    approval_id = decision.get("approval_id")
    approval = approval_by_id.get(approval_id)
    if approval is None:
        raise ContractError("required approval record is missing")
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
