from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from causalperf_reference.artifacts import digest, verify_content_digest


class ExecutionState(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PREPARING_ENVIRONMENT = "PREPARING_ENVIRONMENT"
    BUILDING_BASELINE = "BUILDING_BASELINE"
    VERIFYING_BASELINE_CORRECTNESS = "VERIFYING_BASELINE_CORRECTNESS"
    MEASURING_A1 = "MEASURING_A1"
    DIAGNOSING = "DIAGNOSING"
    REGISTERING = "REGISTERING"
    APPLYING_INTERVENTION = "APPLYING_INTERVENTION"
    BUILDING_TREATMENT = "BUILDING_TREATMENT"
    VERIFYING_TREATMENT_CORRECTNESS = "VERIFYING_TREATMENT_CORRECTNESS"
    MEASURING_B = "MEASURING_B"
    RESTORING_BASELINE = "RESTORING_BASELINE"
    MEASURING_A2 = "MEASURING_A2"
    VERIFYING = "VERIFYING"
    DECIDING = "DECIDING"
    CLEANING_UP = "CLEANING_UP"
    ROLLING_BACK = "ROLLING_BACK"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"


TERMINAL_STATES = {
    ExecutionState.COMPLETED, ExecutionState.REJECTED, ExecutionState.INCONCLUSIVE,
    ExecutionState.FAILED, ExecutionState.ROLLBACK_REQUIRED,
}


@dataclass(frozen=True)
class TransitionSpec:
    state: ExecutionState
    next_state: ExecutionState
    mutating: bool
    idempotent: bool
    safe_boundary_after: bool
    failure_outcome: ExecutionState


@dataclass(frozen=True)
class StepResult:
    status: str
    output_digests: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    decision: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError(f"invalid step status: {self.status}")


@dataclass
class ExecutionSnapshot:
    run_id: str
    state: ExecutionState = ExecutionState.CREATED
    last_safe_state: ExecutionState = ExecutionState.CREATED
    pending_state: ExecutionState | None = None
    mutation_in_flight: bool = False
    intervention_applied: bool = False
    retry_counts: dict[str, int] = field(default_factory=dict)
    artifact_digests: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    policy_sha256: str | None = None
    budget_usage: dict[str, int] = field(default_factory=dict)
    rollback_obligations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        value = asdict(self)
        for key in ("state", "last_safe_state", "pending_state"):
            if value[key] is not None:
                value[key] = value[key].value
        return value

    def to_document(self, ledger_head_sha256: str | None) -> dict:
        value = {"schema_version": 1, **self.to_dict(), "ledger_head_sha256": ledger_head_sha256}
        value["content_sha256"] = digest(value, omit=("content_sha256",))
        return value

    @classmethod
    def from_document(cls, value: dict) -> "ExecutionSnapshot":
        verify_content_digest(value)
        return cls(
            run_id=value["run_id"], state=ExecutionState(value["state"]),
            last_safe_state=ExecutionState(value["last_safe_state"]),
            pending_state=ExecutionState(value["pending_state"]) if value.get("pending_state") else None,
            mutation_in_flight=value["mutation_in_flight"],
            intervention_applied=value["intervention_applied"],
            retry_counts=dict(value["retry_counts"]),
            artifact_digests=list(value["artifact_digests"]),
            reason_codes=list(value["reason_codes"]),
            policy_sha256=value.get("policy_sha256"),
            budget_usage=dict(value.get("budget_usage", {})),
            rollback_obligations=list(value.get("rollback_obligations", [])),
        )
