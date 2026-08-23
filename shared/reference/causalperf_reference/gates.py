from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from statistics import median


@dataclass(frozen=True)
class Gate:
    status: str
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"status": self.status, "reason_codes": list(self.reason_codes)}


def _path_is_within(path: str, root: str) -> bool:
    """Return true for an exact path or a descendant of a declared root."""
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(root.rstrip("/"))
    return candidate == boundary or boundary in candidate.parents


def verify_integrity(intervention: dict, integrity_inputs: dict) -> Gate:
    """Compute source/patch/scope integrity from sealed source manifests.

    The caller supplies facts (file digests and the patch artifact digest), not
    a gate status. Cross-object digest validation happens before this function.
    """
    manifests = {item["role"]: item for item in integrity_inputs.get("source_manifests", [])}
    missing = [role for role in ("BASELINE", "TREATMENT", "RESTORED") if role not in manifests]
    protected_paths = integrity_inputs.get("protected_paths", [])
    if missing or not protected_paths:
        reasons = [f"SOURCE_MANIFEST_MISSING:{role}" for role in missing]
        if not protected_paths:
            reasons.append("PROTECTED_PATH_POLICY_MISSING")
        return Gate("INCONCLUSIVE", tuple(reasons))

    baseline = manifests["BASELINE"]
    treatment = manifests["TREATMENT"]
    restored = manifests["RESTORED"]
    reasons: list[str] = []
    if baseline["tree_sha256"] != intervention["rollback"]["baseline_source_sha256"]:
        reasons.append("BASELINE_SOURCE_MISMATCH")
    if treatment.get("applied_patch_sha256") != intervention["patch_sha256"]:
        reasons.append("APPLIED_PATCH_MISMATCH")

    before = {item["path"]: item["sha256"] for item in baseline["entries"]}
    after = {item["path"]: item["sha256"] for item in treatment["entries"]}
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if not changed:
        reasons.append("NO_SOURCE_CHANGE")
    allowed = intervention["allowed_paths"]
    if any(not any(_path_is_within(path, root) for root in allowed) for path in changed):
        reasons.append("CHANGE_OUTSIDE_ALLOWED_PATHS")
    if any(any(_path_is_within(path, root) for root in protected_paths) for path in changed):
        reasons.append("PROTECTED_PATH_CHANGED")
    if restored["tree_sha256"] != baseline["tree_sha256"] or restored["entries"] != baseline["entries"]:
        reasons.append("BASELINE_NOT_RESTORED")
    return Gate("FAIL", tuple(reasons)) if reasons else Gate("PASS")


def verify_correctness(correctness_reports: list[dict]) -> Gate:
    """Compute correctness from raw baseline/treatment test-run outputs."""
    reports = {item["phase"]: item for item in correctness_reports}
    missing = [phase for phase in ("BASELINE", "TREATMENT") if phase not in reports]
    if missing:
        return Gate("INCONCLUSIVE", tuple(f"CORRECTNESS_REPORT_MISSING:{phase}" for phase in missing))

    baseline = reports["BASELINE"]
    treatment = reports["TREATMENT"]
    failures: list[str] = []
    inconclusive: list[str] = []
    if (baseline["suite_id"], baseline["suite_sha256"]) != (treatment["suite_id"], treatment["suite_sha256"]):
        failures.append("CORRECTNESS_SUITE_CHANGED")
    for phase, report in (("BASELINE", baseline), ("TREATMENT", treatment)):
        if report["exit_code"] != 0:
            failures.append(f"CORRECTNESS_COMMAND_FAILED:{phase}")
        if report["failure_count"] > 0:
            failures.append(f"CORRECTNESS_ASSERTIONS_FAILED:{phase}")
        if report["test_count"] == 0:
            inconclusive.append(f"NO_CORRECTNESS_TESTS:{phase}")
    if treatment["test_count"] < baseline["test_count"]:
        failures.append("CORRECTNESS_TEST_COUNT_REDUCED")
    if treatment["skipped_count"] > baseline["skipped_count"]:
        failures.append("CORRECTNESS_SKIPS_INCREASED")

    baseline_behavior = baseline.get("behavior_sha256")
    treatment_behavior = treatment.get("behavior_sha256")
    if bool(baseline_behavior) != bool(treatment_behavior):
        inconclusive.append("BEHAVIOR_DIGEST_INCOMPLETE")
    elif baseline_behavior and baseline_behavior != treatment_behavior:
        failures.append("PROTECTED_BEHAVIOR_CHANGED")
    if failures:
        return Gate("FAIL", tuple(failures + inconclusive))
    if inconclusive:
        return Gate("INCONCLUSIVE", tuple(inconclusive))
    return Gate("PASS")


def verify_intervention_isolation(intervention: dict) -> Gate:
    """A declared multi-factor change may support E1, but never C1."""
    if intervention.get("additional_factors"):
        return Gate("FAIL", ("MULTI_FACTOR_INTERVENTION_NOT_ISOLATED",))
    return Gate("PASS")


def verify_environment(environments: list[dict], policy: dict | None = None) -> Gate:
    if not environments:
        return Gate("INCONCLUSIVE", ("NO_ENVIRONMENT_SNAPSHOTS",))
    if policy is None:
        return Gate("INCONCLUSIVE", ("ENVIRONMENT_POLICY_MISSING",))
    reasons: list[str] = []
    for item in environments:
        identifier = item["id"]
        device = item["device"]
        runtime = item["runtime"]
        api = policy["api_level"]
        if not api["minimum"] <= device["api_level"] <= api["maximum"]:
            reasons.append(f"API_LEVEL_OUT_OF_POLICY:{identifier}")
        if device["abi"] not in policy["allowed_abis"]:
            reasons.append(f"ABI_OUT_OF_POLICY:{identifier}")
        if runtime["battery_percent"] < policy["min_battery_percent"]:
            reasons.append(f"BATTERY_BELOW_POLICY:{identifier}")
        if policy["charging"] == "REQUIRED" and not runtime["charging"]:
            reasons.append(f"CHARGING_REQUIRED:{identifier}")
        if policy["charging"] == "FORBIDDEN" and runtime["charging"]:
            reasons.append(f"CHARGING_FORBIDDEN:{identifier}")
        if runtime["thermal_status"] not in policy["allowed_thermal_statuses"]:
            reasons.append(f"THERMAL_STATUS_OUT_OF_POLICY:{identifier}")
        if runtime["online_cpu_count"] != policy["expected_online_cpu_count"]:
            reasons.append(f"ONLINE_CPU_COUNT_CHANGED:{identifier}")
        if runtime["available_memory_mb"] < policy["min_available_memory_mb"]:
            reasons.append(f"AVAILABLE_MEMORY_BELOW_POLICY:{identifier}")
        if runtime["background_load_percent"] > policy["max_background_load_percent"]:
            reasons.append(f"BACKGROUND_LOAD_ABOVE_POLICY:{identifier}")
        if runtime["compilation_mode"] != policy["compilation_mode"]:
            reasons.append(f"COMPILATION_MODE_CHANGED:{identifier}")
    if reasons:
        return Gate("INCONCLUSIVE", tuple(reasons))
    signatures = {
        (item["device"]["serial_hash"], item["device"]["model"], item["device"]["abi"],
         item["device"]["api_level"], item["device"]["build_fingerprint_sha256"],
         item["runtime"]["compilation_mode"], tuple(sorted(item["toolchain"].items())))
        for item in environments
    }
    if len(signatures) != 1:
        return Gate("INCONCLUSIVE", ("ENVIRONMENT_IDENTITY_CHANGED",))
    return Gate("PASS")


def verify_mechanism(prediction: dict, evidence_by_arm: dict[str, list[dict]]) -> Gate:
    reasons: list[str] = []
    baseline = evidence_by_arm.get("A1", []) + evidence_by_arm.get("A2", [])
    treatment = evidence_by_arm.get("B", [])
    for expected in prediction["expected_mechanism_change"]:
        metric = expected["metric"]
        before = [float(item["value"]) for item in baseline if item["metric"] == metric and item.get("validity", "VALID") == "VALID" and isinstance(item["value"], (int, float))]
        after = [float(item["value"]) for item in treatment if item["metric"] == metric and item.get("validity", "VALID") == "VALID" and isinstance(item["value"], (int, float))]
        direction = expected["direction"]
        if direction == "present":
            passed = bool(after)
        elif direction == "absent":
            passed = not after
        elif not before or not after:
            reasons.append(f"MISSING_MECHANISM_EVIDENCE:{metric}")
            continue
        else:
            delta = median(after) - median(before)
            passed = delta > 0 if direction == "increase" else delta < 0
        if not passed:
            reasons.append(f"MECHANISM_DIRECTION_MISMATCH:{metric}")
    if any(reason.startswith("MISSING_") for reason in reasons):
        return Gate("INCONCLUSIVE", tuple(reasons))
    return Gate("FAIL", tuple(reasons)) if reasons else Gate("PASS")


def verify_replication(primary_effect_ms: float, replication_effects_ms: list[float], *, tolerance_percent: float = 20) -> Gate:
    if not replication_effects_ms:
        return Gate("INCONCLUSIVE", ("REPLICATION_MISSING",))
    if primary_effect_ms <= 0:
        return Gate("FAIL", ("PRIMARY_EFFECT_NOT_POSITIVE",))
    lower = primary_effect_ms * (1 - tolerance_percent / 100)
    if any(effect <= 0 for effect in replication_effects_ms):
        return Gate("FAIL", ("REPLICATION_DIRECTION_MISMATCH",))
    if median(replication_effects_ms) < lower:
        return Gate("INCONCLUSIVE", ("REPLICATION_EFFECT_OUTSIDE_TOLERANCE",))
    return Gate("PASS")
