from __future__ import annotations

from dataclasses import dataclass, field

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult

from .environment import AndroidEnvironmentCollector, AndroidLabRequirements, PreflightResult


@dataclass
class PreflightExecutionAdapter:
    """Require Android lab preflight at the controller environment boundary."""

    delegate: ExecutionAdapter
    collector: AndroidEnvironmentCollector
    device_serial: str = field(repr=False)
    environment_id: str
    requirements: AndroidLabRequirements = field(default_factory=AndroidLabRequirements)
    last_result: PreflightResult | None = field(default=None, init=False)

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        if state != ExecutionState.PREPARING_ENVIRONMENT:
            return self.delegate.execute(state, snapshot)
        self.last_result = self.collector.collect(
            device_serial=self.device_serial,
            environment_id=self.environment_id,
            requirements=self.requirements,
        )
        if self.last_result.status != "PASS" or self.last_result.environment_snapshot is None:
            return StepResult("INCONCLUSIVE", reason_codes=self.last_result.reason_codes)
        return StepResult(
            "PASS",
            output_digests=(self.last_result.environment_snapshot["content_sha256"],),
        )

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)
