from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median


@dataclass(frozen=True)
class Gate:
    status: str
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def verify_environment(environments: list[dict]) -> Gate:
    if not environments:
        return Gate("INCONCLUSIVE", ("NO_ENVIRONMENT_SNAPSHOTS",))
    invalid = [item["id"] for item in environments if item["validity"]["status"] != "PASS"]
    if invalid:
        return Gate("INCONCLUSIVE", ("ENVIRONMENT_SNAPSHOT_INVALID",))
    signatures = {
        (item["device"]["model"], item["device"]["abi"], item["device"]["api_level"],
         item["device"]["build_fingerprint_sha256"], item["runtime"]["compilation_mode"])
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
