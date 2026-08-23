from __future__ import annotations

from dataclasses import dataclass

from causalperf_reference.artifacts import digest

from .adapter import RecoveryObservation, TransportError
from .model import ExecutionSnapshot, ExecutionState, StepResult


class InjectedCrash(RuntimeError):
    pass


@dataclass
class SimulatedAdapter:
    fail_at: dict[ExecutionState, str] | None = None
    crash_after_mutation_at: set[ExecutionState] | None = None
    rollback_fails: bool = False
    accepted: bool = True
    transport_failures: dict[ExecutionState, int] | None = None

    def __post_init__(self) -> None:
        self.fail_at = dict(self.fail_at or {})
        self.crash_after_mutation_at = set(self.crash_after_mutation_at or set())
        self.transport_failures = dict(self.transport_failures or {})
        self.workspace = "BASELINE"
        self.device = "BASELINE"
        self.executed: list[ExecutionState] = []

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        self.executed.append(state)
        remaining_transport_failures = self.transport_failures.get(state, 0)
        if remaining_transport_failures:
            self.transport_failures[state] = remaining_transport_failures - 1
            raise TransportError(f"simulated transport failure in {state.value}")
        failure = self.fail_at.get(state)
        if failure:
            status = "INCONCLUSIVE" if failure == "INCONCLUSIVE" else "FAIL"
            return StepResult(status, reason_codes=(f"SIMULATED_{failure}:{state.value}",))

        if state == ExecutionState.APPLYING_INTERVENTION:
            self.workspace = "TREATMENT_SOURCE"
        elif state == ExecutionState.BUILDING_TREATMENT:
            self.device = "TREATMENT_APK"
        elif state == ExecutionState.RESTORING_BASELINE:
            self.workspace = "BASELINE"
            self.device = "BASELINE"
        elif state == ExecutionState.CLEANING_UP:
            self.device = "CLEAN"

        if state in self.crash_after_mutation_at:
            raise InjectedCrash(f"crash after mutation in {state.value}")

        decision = None
        if state == ExecutionState.DECIDING:
            decision = "ACCEPT" if self.accepted else "REJECT"
        output = digest({"run_id": snapshot.run_id, "state": state.value, "ordinal": len(self.executed)})
        return StepResult("PASS", (output,), decision=decision)

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return RecoveryObservation(self.workspace, self.device, self.workspace == "BASELINE")

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        if self.rollback_fails:
            return StepResult("FAIL", reason_codes=("SIMULATED_ROLLBACK_FAILURE",))
        self.workspace = "BASELINE"
        self.device = "BASELINE"
        return StepResult("PASS", (digest({"run_id": snapshot.run_id, "rollback": "BASELINE"}),))
