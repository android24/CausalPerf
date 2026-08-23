import json
import tempfile
import unittest
from pathlib import Path

from causalperf_agent.execution import CheckpointError, ExperimentController, ExecutionState, FileRunStore, InjectedCrash, SimulatedAdapter
from causalperf_agent.execution.transitions import ORDERED_TRANSITIONS


class Clock:
    def __init__(self):
        self.second = 0

    def __call__(self):
        value = f"2026-01-01T00:00:{self.second:02d}Z"
        self.second += 1
        return value


class ExperimentControllerTest(unittest.TestCase):
    def controller(self, adapter):
        return ExperimentController("RUN-1", adapter, clock=Clock())

    def test_complete_simulated_experiment(self):
        controller = self.controller(SimulatedAdapter())
        result = controller.run()
        self.assertEqual(result.state, ExecutionState.COMPLETED)
        self.assertEqual(len(controller.ledger.events), 2 * len(ORDERED_TRANSITIONS))
        self.assertEqual(controller.ledger.verify(), controller.ledger.events[-1]["event_sha256"])
        for first, second in zip(controller.ledger.events[::2], controller.ledger.events[1::2]):
            self.assertEqual((first["kind"], second["kind"]), ("INTENT", "COMPLETION"))
            self.assertEqual(first["phase"], second["phase"])

    def test_rejected_decision_rolls_back(self):
        adapter = SimulatedAdapter(accepted=False)
        controller = self.controller(adapter)
        result = controller.run()
        self.assertEqual(result.state, ExecutionState.REJECTED)
        self.assertEqual(adapter.workspace, "BASELINE")
        self.assertEqual(adapter.device, "BASELINE")
        self.assertEqual(controller.ledger.events[-2]["kind"], "INTENT")
        self.assertEqual(controller.ledger.events[-1]["kind"], "COMPLETION")

    def test_correctness_failure_rolls_back_and_rejects(self):
        state = ExecutionState.VERIFYING_TREATMENT_CORRECTNESS
        adapter = SimulatedAdapter(fail_at={state: "FAIL"})
        result = self.controller(adapter).run()
        self.assertEqual(result.state, ExecutionState.REJECTED)
        self.assertEqual(adapter.workspace, "BASELINE")

    def test_environment_deficiency_is_inconclusive_without_mutation(self):
        state = ExecutionState.PREPARING_ENVIRONMENT
        adapter = SimulatedAdapter(fail_at={state: "INCONCLUSIVE"})
        result = self.controller(adapter).run()
        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertNotIn(ExecutionState.ROLLING_BACK, adapter.executed)

    def test_crash_after_every_mutating_phase_recovers_by_rollback(self):
        mutating = [item.state for item in ORDERED_TRANSITIONS if item.mutating]
        for state in mutating:
            with self.subTest(state=state):
                adapter = SimulatedAdapter(crash_after_mutation_at={state})
                controller = self.controller(adapter)
                with self.assertRaises(InjectedCrash):
                    controller.run()
                self.assertEqual(controller.snapshot.pending_state, state)
                result = controller.recover()
                self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
                self.assertEqual(adapter.workspace, "BASELINE")
                self.assertEqual(adapter.device, "BASELINE")

    def test_failure_at_every_phase_has_a_declared_terminal_outcome(self):
        for spec in ORDERED_TRANSITIONS:
            with self.subTest(state=spec.state):
                adapter = SimulatedAdapter(fail_at={spec.state: "FAIL"})
                result = self.controller(adapter).run()
                self.assertIn(result.state, {
                    ExecutionState.FAILED, ExecutionState.REJECTED,
                    ExecutionState.INCONCLUSIVE, ExecutionState.ROLLBACK_REQUIRED,
                })

    def test_crash_at_every_phase_recovers_without_assuming_completion(self):
        for spec in ORDERED_TRANSITIONS:
            with self.subTest(state=spec.state):
                adapter = SimulatedAdapter(crash_after_mutation_at={spec.state})
                controller = self.controller(adapter)
                with self.assertRaises(InjectedCrash):
                    controller.run()
                completed_before_recovery = sum(
                    event["phase"] == spec.state.value and event["kind"] == "COMPLETION"
                    for event in controller.ledger.events
                )
                self.assertEqual(completed_before_recovery, 0)
                adapter.crash_after_mutation_at.clear()
                result = controller.recover()
                self.assertIn(result.state, {
                    ExecutionState.COMPLETED, ExecutionState.INCONCLUSIVE,
                    ExecutionState.ROLLBACK_REQUIRED,
                })

    def test_failed_recovery_requires_manual_rollback(self):
        state = ExecutionState.APPLYING_INTERVENTION
        adapter = SimulatedAdapter(crash_after_mutation_at={state}, rollback_fails=True)
        controller = self.controller(adapter)
        with self.assertRaises(InjectedCrash):
            controller.run()
        result = controller.recover()
        self.assertEqual(result.state, ExecutionState.ROLLBACK_REQUIRED)

    def test_new_controller_recovers_from_json_snapshot_and_ledger(self):
        state = ExecutionState.APPLYING_INTERVENTION
        adapter = SimulatedAdapter(crash_after_mutation_at={state})
        controller = self.controller(adapter)
        with self.assertRaises(InjectedCrash):
            controller.run()
        snapshot_document = json.loads(json.dumps(controller.snapshot_document()))
        ledger_events = json.loads(json.dumps(controller.ledger.events))
        adapter.crash_after_mutation_at.clear()
        restored = ExperimentController.restore(snapshot_document, ledger_events, adapter, clock=Clock())
        result = restored.recover()
        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertEqual(adapter.workspace, "BASELINE")
        self.assertTrue(restored.ledger.verify())

    def test_new_process_recovers_from_atomic_file_checkpoint(self):
        state = ExecutionState.APPLYING_INTERVENTION
        adapter = SimulatedAdapter(crash_after_mutation_at={state})
        with tempfile.TemporaryDirectory() as directory:
            store = FileRunStore(Path(directory) / "RUN-1")
            controller = ExperimentController("RUN-1", adapter, clock=Clock(), store=store)
            with self.assertRaises(InjectedCrash):
                controller.run()
            persisted_snapshot, _ = store.load()
            self.assertEqual(persisted_snapshot["pending_state"], state.value)
            self.assertTrue(persisted_snapshot["mutation_in_flight"])
            adapter.crash_after_mutation_at.clear()
            restored = ExperimentController.restore_from_store(store, adapter, clock=Clock())
            result = restored.recover()
            self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
            self.assertEqual(store.load()[0]["state"], ExecutionState.INCONCLUSIVE.value)

    def test_tampered_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileRunStore(Path(directory) / "RUN-1")
            controller = ExperimentController("RUN-1", SimulatedAdapter(), clock=Clock(), store=store)
            controller.run()
            value = json.loads(store.path.read_text())
            value["snapshot"]["state"] = ExecutionState.FAILED.value
            store.path.write_text(json.dumps(value))
            with self.assertRaisesRegex(CheckpointError, "digest"):
                store.load()

    def test_non_idempotent_measurement_crash_is_not_retried(self):
        state = ExecutionState.MEASURING_A1
        adapter = SimulatedAdapter(crash_after_mutation_at={state})
        controller = self.controller(adapter)
        with self.assertRaises(InjectedCrash):
            controller.run()
        adapter.crash_after_mutation_at.clear()
        count = adapter.executed.count(state)
        result = controller.recover()
        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertEqual(adapter.executed.count(state), count)

    def test_safe_transport_failure_is_retried_once(self):
        state = ExecutionState.PREPARING_ENVIRONMENT
        adapter = SimulatedAdapter(transport_failures={state: 1})
        result = self.controller(adapter).run()
        self.assertEqual(result.state, ExecutionState.COMPLETED)
        self.assertEqual(adapter.executed.count(state), 2)
        self.assertEqual(result.retry_counts[state.value], 1)

    def test_non_idempotent_transport_failure_is_not_retried(self):
        state = ExecutionState.MEASURING_A1
        adapter = SimulatedAdapter(transport_failures={state: 1})
        result = self.controller(adapter).run()
        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertEqual(adapter.executed.count(state), 1)

    def test_mutating_transport_failure_rolls_back_without_retry(self):
        state = ExecutionState.APPLYING_INTERVENTION
        adapter = SimulatedAdapter(transport_failures={state: 1})
        result = self.controller(adapter).run()
        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertEqual(adapter.executed.count(state), 1)
        self.assertEqual(adapter.workspace, "BASELINE")


if __name__ == "__main__":
    unittest.main()
