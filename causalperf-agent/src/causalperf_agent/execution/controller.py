from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from causalperf_reference.ledger import Ledger

from .adapter import ExecutionAdapter, TransportError
from .model import ExecutionSnapshot, ExecutionState, TERMINAL_STATES, TransitionSpec
from .store import FileRunStore
from .transitions import INITIAL_STATE, TRANSITIONS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ExperimentController:
    def __init__(self, run_id: str, adapter: ExecutionAdapter, *, ledger: Ledger | None = None,
                 clock: Callable[[], str] = _utc_now, store: FileRunStore | None = None):
        self.adapter = adapter
        self.ledger = ledger or Ledger(run_id)
        self.clock = clock
        self.store = store
        self.snapshot = ExecutionSnapshot(run_id=run_id)

    @classmethod
    def restore(cls, snapshot_document: dict, ledger_events: list[dict], adapter: ExecutionAdapter,
                *, clock: Callable[[], str] = _utc_now,
                store: FileRunStore | None = None) -> "ExperimentController":
        snapshot = ExecutionSnapshot.from_document(snapshot_document)
        ledger = Ledger.from_events(snapshot.run_id, ledger_events)
        if snapshot_document["ledger_head_sha256"] != ledger.verify():
            raise ValueError("snapshot ledger head mismatch")
        controller = cls(snapshot.run_id, adapter, ledger=ledger, clock=clock, store=store)
        controller.snapshot = snapshot
        return controller

    @classmethod
    def restore_from_store(cls, store: FileRunStore, adapter: ExecutionAdapter,
                           *, clock: Callable[[], str] = _utc_now) -> "ExperimentController":
        snapshot, events = store.load()
        return cls.restore(snapshot, events, adapter, clock=clock, store=store)

    def snapshot_document(self) -> dict:
        return self.snapshot.to_document(self.ledger.verify())

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save(self.snapshot_document(), self.ledger.events)

    def _event(self, phase: ExecutionState, kind: str, *, outputs: tuple[str, ...] = (),
               exit_status: int | None = None) -> None:
        event = {"occurred_at": self.clock(), "actor": "RUNNER", "phase": phase.value,
                 "kind": kind, "inputs": tuple(self.snapshot.artifact_digests), "outputs": outputs}
        if exit_status is not None:
            event["exit_status"] = exit_status
        self.ledger.append(event)
        self._persist()

    def _complete(self, spec: TransitionSpec, outputs: tuple[str, ...]) -> None:
        self._event(spec.state, "COMPLETION", outputs=outputs, exit_status=0)
        self.snapshot.artifact_digests.extend(outputs)
        self.snapshot.pending_state = None
        self.snapshot.mutation_in_flight = False
        if spec.state == ExecutionState.APPLYING_INTERVENTION:
            self.snapshot.intervention_applied = True
        elif spec.state == ExecutionState.RESTORING_BASELINE:
            self.snapshot.intervention_applied = False
        self.snapshot.state = spec.next_state
        if spec.safe_boundary_after:
            self.snapshot.last_safe_state = spec.next_state
        self._persist()

    def _rollback(self, outcome: ExecutionState) -> ExecutionSnapshot:
        self.snapshot.state = ExecutionState.ROLLING_BACK
        self.snapshot.pending_state = ExecutionState.ROLLING_BACK
        self.snapshot.mutation_in_flight = True
        self._event(ExecutionState.ROLLING_BACK, "INTENT")
        result = self.adapter.rollback(self.snapshot)
        if result.status != "PASS":
            self.snapshot.reason_codes.extend(result.reason_codes)
            self._event(ExecutionState.ROLLING_BACK, "FAILURE", exit_status=1)
            self.snapshot.state = ExecutionState.ROLLBACK_REQUIRED
            self._persist()
            return self.snapshot
        self._event(ExecutionState.ROLLING_BACK, "COMPLETION", outputs=result.output_digests, exit_status=0)
        self.snapshot.artifact_digests.extend(result.output_digests)
        self.snapshot.pending_state = None
        self.snapshot.mutation_in_flight = False
        self.snapshot.intervention_applied = False
        self.snapshot.state = outcome
        self.snapshot.last_safe_state = outcome
        self._persist()
        return self.snapshot

    def _handle_failure(self, spec: TransitionSpec, result) -> ExecutionSnapshot:
        self.snapshot.reason_codes.extend(result.reason_codes)
        self._event(spec.state, "FAILURE", outputs=result.output_digests, exit_status=1)
        self.snapshot.pending_state = None
        needs_rollback = self.snapshot.intervention_applied or spec.mutating
        self.snapshot.mutation_in_flight = False
        outcome = ExecutionState.INCONCLUSIVE if result.status == "INCONCLUSIVE" else spec.failure_outcome
        if needs_rollback:
            return self._rollback(outcome)
        self.snapshot.state = outcome
        self._persist()
        return self.snapshot

    def run(self) -> ExecutionSnapshot:
        if self.snapshot.state == ExecutionState.CREATED:
            self.snapshot.state = INITIAL_STATE
        while self.snapshot.state not in TERMINAL_STATES:
            spec = TRANSITIONS.get(self.snapshot.state)
            if spec is None:
                raise RuntimeError(f"no transition for {self.snapshot.state.value}")
            authorize = getattr(self.adapter, "authorize", None)
            if authorize is not None:
                authorization = authorize(spec.state, self.snapshot)
                if authorization.status != "PASS":
                    self.snapshot.reason_codes.extend(authorization.reason_codes)
                    self._event(spec.state, "FAILURE", outputs=authorization.output_digests, exit_status=1)
                    self.snapshot.state = (
                        ExecutionState.INCONCLUSIVE
                        if authorization.status == "INCONCLUSIVE"
                        else spec.failure_outcome
                    )
                    self._persist()
                    return self.snapshot
                self._event(spec.state, "APPROVAL", outputs=authorization.output_digests, exit_status=0)
            self.snapshot.pending_state = spec.state
            self.snapshot.mutation_in_flight = spec.mutating
            self._event(spec.state, "INTENT")
            try:
                result = self.adapter.execute(spec.state, self.snapshot)
            except TransportError:
                retries = self.snapshot.retry_counts.get(spec.state.value, 0)
                retry_safe = spec.idempotent and not spec.mutating and retries < 1
                self._event(spec.state, "FAILURE", exit_status=1)
                self.snapshot.reason_codes.append(f"TRANSPORT_ERROR:{spec.state.value}")
                if retry_safe:
                    self.snapshot.retry_counts[spec.state.value] = retries + 1
                    self.snapshot.pending_state = None
                    self.snapshot.mutation_in_flight = False
                    self._persist()
                    continue
                self.snapshot.pending_state = None
                if spec.mutating or self.snapshot.intervention_applied:
                    return self._rollback(ExecutionState.INCONCLUSIVE)
                self.snapshot.mutation_in_flight = False
                self.snapshot.state = ExecutionState.INCONCLUSIVE
                self._persist()
                return self.snapshot
            if result.status != "PASS":
                return self._handle_failure(spec, result)
            self._complete(spec, result.output_digests)
            if spec.state == ExecutionState.DECIDING and result.decision == "REJECT":
                return self._rollback(ExecutionState.REJECTED)
        return self.snapshot

    def recover(self) -> ExecutionSnapshot:
        """Recover an interrupted controller without assuming an intent completed."""
        pending = self.snapshot.pending_state
        if pending is None:
            return self.run()
        spec = TRANSITIONS.get(pending)
        if spec is None:
            return self._rollback(ExecutionState.INCONCLUSIVE)
        observation = self.adapter.inspect_recovery(self.snapshot)
        self._event(pending, "FAILURE", exit_status=1)
        self.snapshot.reason_codes.append(f"INTERRUPTED:{pending.value}")
        if self.snapshot.mutation_in_flight or self.snapshot.intervention_applied:
            return self._rollback(ExecutionState.INCONCLUSIVE)
        self.snapshot.pending_state = None
        self.snapshot.mutation_in_flight = False
        if spec.idempotent and not observation.owned_processes_running:
            self.snapshot.state = pending
            return self.run()
        self.snapshot.state = ExecutionState.INCONCLUSIVE
        self._persist()
        return self.snapshot
