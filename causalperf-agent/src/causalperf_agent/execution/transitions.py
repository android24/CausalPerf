from __future__ import annotations

from .model import ExecutionState as S, TransitionSpec


ORDERED_TRANSITIONS = (
    TransitionSpec(S.VALIDATING, S.PREPARING_ENVIRONMENT, False, True, True, S.INCONCLUSIVE),
    TransitionSpec(S.PREPARING_ENVIRONMENT, S.BUILDING_BASELINE, False, True, True, S.INCONCLUSIVE),
    TransitionSpec(S.BUILDING_BASELINE, S.VERIFYING_BASELINE_CORRECTNESS, True, False, True, S.FAILED),
    TransitionSpec(S.VERIFYING_BASELINE_CORRECTNESS, S.MEASURING_A1, False, True, True, S.FAILED),
    TransitionSpec(S.MEASURING_A1, S.DIAGNOSING, False, False, True, S.INCONCLUSIVE),
    TransitionSpec(S.DIAGNOSING, S.REGISTERING, False, True, True, S.INCONCLUSIVE),
    TransitionSpec(S.REGISTERING, S.APPLYING_INTERVENTION, False, True, True, S.INCONCLUSIVE),
    TransitionSpec(S.APPLYING_INTERVENTION, S.BUILDING_TREATMENT, True, False, True, S.FAILED),
    TransitionSpec(S.BUILDING_TREATMENT, S.VERIFYING_TREATMENT_CORRECTNESS, True, False, True, S.FAILED),
    TransitionSpec(S.VERIFYING_TREATMENT_CORRECTNESS, S.MEASURING_B, False, True, True, S.REJECTED),
    TransitionSpec(S.MEASURING_B, S.RESTORING_BASELINE, False, False, True, S.INCONCLUSIVE),
    TransitionSpec(S.RESTORING_BASELINE, S.MEASURING_A2, True, False, True, S.FAILED),
    TransitionSpec(S.MEASURING_A2, S.VERIFYING, False, False, True, S.INCONCLUSIVE),
    TransitionSpec(S.VERIFYING, S.DECIDING, False, True, True, S.INCONCLUSIVE),
    TransitionSpec(S.DECIDING, S.CLEANING_UP, False, True, True, S.REJECTED),
    TransitionSpec(S.CLEANING_UP, S.COMPLETED, True, True, True, S.ROLLBACK_REQUIRED),
)

TRANSITIONS = {item.state: item for item in ORDERED_TRANSITIONS}
INITIAL_STATE = ORDERED_TRANSITIONS[0].state

