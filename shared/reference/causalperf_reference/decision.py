from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass(frozen=True)
class CausalDecision:
    verdict: str
    support_level: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def decide(
    *,
    prediction_registered_at: str,
    first_treatment_at: str,
    integrity: str,
    correctness: str,
    environment: str,
    mechanism: str,
    statistics: str,
    replication: str,
    isolation: str = "PASS",
) -> CausalDecision:
    registered = datetime.fromisoformat(prediction_registered_at.replace("Z", "+00:00"))
    treatment = datetime.fromisoformat(first_treatment_at.replace("Z", "+00:00"))
    if registered >= treatment:
        return CausalDecision("INVALID", "NONE", ("PREDICTION_NOT_PREREGISTERED",))
    if integrity == "INCONCLUSIVE":
        return CausalDecision("INCONCLUSIVE", "O1", ("INTEGRITY_EVIDENCE_INSUFFICIENT",))
    if integrity != "PASS":
        return CausalDecision("INVALID", "NONE", ("INTEGRITY_FAILED",))
    if correctness == "INCONCLUSIVE":
        return CausalDecision("INCONCLUSIVE", "O1", ("CORRECTNESS_EVIDENCE_INSUFFICIENT",))
    if correctness != "PASS":
        return CausalDecision("REJECT", "NONE", ("CORRECTNESS_FAILED",))
    if environment != "PASS":
        return CausalDecision("INCONCLUSIVE", "O1", ("ENVIRONMENT_INVALID",))
    if mechanism == "INCONCLUSIVE":
        return CausalDecision("INCONCLUSIVE", "O1", ("MECHANISM_EVIDENCE_INSUFFICIENT",))
    if mechanism != "PASS":
        return CausalDecision("REJECT", "O1", ("MECHANISM_PREDICTION_FAILED",))
    if statistics == "FAIL":
        return CausalDecision("REJECT", "O1", ("PERFORMANCE_PREDICTION_FAILED",))
    if statistics != "PASS":
        return CausalDecision("INCONCLUSIVE", "O1", ("STATISTICS_INCONCLUSIVE",))
    if replication == "FAIL":
        return CausalDecision("REJECT", "E1", ("REPLICATION_FAILED",))
    if replication != "PASS":
        return CausalDecision("EXPERIMENTALLY_SUPPORTED", "E1", ("REPLICATION_REQUIRED",))
    if isolation != "PASS":
        return CausalDecision("EXPERIMENTALLY_SUPPORTED", "E1", ("INTERVENTION_NOT_ISOLATED",))
    return CausalDecision("CAUSALLY_SUPPORTED", "C1", ())
