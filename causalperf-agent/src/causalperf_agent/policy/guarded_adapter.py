from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from causalperf_reference.artifacts import digest

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult

from .engine import PolicyEngine
from .audit import FileToolCallAuditStore
from .model import ToolRequest


RequestFactory = Callable[[ExecutionState, ExecutionSnapshot], ToolRequest | None]


@dataclass
class GuardedExecutionAdapter:
    delegate: ExecutionAdapter
    policy_engine: PolicyEngine
    request_factory: RequestFactory
    audit_records: list[dict] = field(default_factory=list)
    audit_store: FileToolCallAuditStore | None = None
    _authorized: list[tuple[ExecutionState, int | None]] = field(default_factory=list)
    _authorization_times: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audit_store is not None:
            persisted = self.audit_store.load()
            if self.audit_records and self.audit_records != persisted:
                raise ValueError("in-memory and persisted ToolCall audits differ")
            self.audit_records = persisted
            interrupted = False
            for record in self.audit_records:
                if record["status"] == "RUNNING":
                    record["completed_at"] = self.policy_engine.clock()
                    record["status"] = "FAILED"
                    record["exit_code"] = 1
                    record["failure_code"] = "INTERRUPTED_BEFORE_COMPLETION"
                    interrupted = True
            if interrupted:
                self._persist_audit()

    def _persist_audit(self) -> None:
        if self.audit_store is not None:
            self.audit_store.save(self.audit_records)

    def _record(self, request: ToolRequest, decision) -> int:
        policy_decision = {
            "status": decision.status,
            "reason_codes": list(decision.reason_codes),
        }
        if decision.approval_id:
            policy_decision["approval_id"] = decision.approval_id
        status = {
            "ALLOW": "REQUESTED",
            "DENY": "DENIED",
            "REQUIRE_APPROVAL": "APPROVAL_PENDING",
        }[decision.status]
        self.audit_records.append({
            "schema_version": 1,
            "id": f"TC-{len(self.audit_records) + 1:06d}",
            "run_id": self.policy_engine.policy.run_id,
            "requested_at": request.requested_at,
            "tool_id": request.tool_id,
            "risk": decision.risk,
            "request_sha256": request.request_sha256,
            "arguments": request.arguments,
            "policy_decision": policy_decision,
            "status": status,
        })
        index = len(self.audit_records) - 1
        if decision.status == "ALLOW" and decision.authorization_at:
            self._authorization_times[index] = decision.authorization_at
        self._persist_audit()
        return index

    def authorize(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        request = self.request_factory(state, snapshot)
        if request is None:
            self._authorized.append((state, None))
            return StepResult("PASS")
        decision = self.policy_engine.authorize_and_reserve(request, snapshot)
        audit_index = self._record(request, decision)
        if decision.status != "ALLOW":
            return StepResult("INCONCLUSIVE" if decision.status == "REQUIRE_APPROVAL" else "FAIL",
                              reason_codes=tuple(f"POLICY:{reason}" for reason in decision.reason_codes))
        self._authorized.append((state, audit_index))
        return StepResult("PASS", (digest(decision.to_dict()),))

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        authorized_index = next((index for index, item in enumerate(self._authorized) if item[0] == state), None)
        if authorized_index is None:
            return StepResult("FAIL", reason_codes=("POLICY:AUTHORIZATION_MISSING",))
        _, audit_index = self._authorized.pop(authorized_index)
        if audit_index is not None:
            record = self.audit_records[audit_index]
            record["started_at"] = self._authorization_times.pop(audit_index)
            record["status"] = "RUNNING"
            self._persist_audit()
        result = self.delegate.execute(state, snapshot)
        if audit_index is not None:
            record["completed_at"] = record["started_at"]
            record["status"] = "SUCCEEDED" if result.status == "PASS" else "FAILED"
            record["exit_code"] = 0 if result.status == "PASS" else 1
            if result.reason_codes:
                record["failure_code"] = result.reason_codes[0]
            self._persist_audit()
        if result.status == "PASS" and state == ExecutionState.RESTORING_BASELINE:
            self.policy_engine.complete_rollback(snapshot)
        return result

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        result = self.delegate.rollback(snapshot)
        if result.status == "PASS":
            self.policy_engine.complete_rollback(snapshot)
        return result
