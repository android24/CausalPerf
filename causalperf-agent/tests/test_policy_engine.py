import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.execution import (
    ExecutionSnapshot, ExperimentController, ExecutionState, FileRunStore,
    InjectedCrash, SimulatedAdapter,
)
from causalperf_agent.policy import (
    FileToolCallAuditStore, GuardedExecutionAdapter, PolicyEngine, RuntimePolicy,
    ToolRequest,
)
from causalperf_reference.artifacts import digest, verify_tool_approval


SHA = "a" * 64
AUTHORIZATION_TIME = "2026-01-01T00:00:10Z"
AGENT_ROOT = Path(__file__).parents[1]
REPO_ROOT = Path(__file__).parents[2]
RUNTIME_POLICY_SCHEMA = json.loads((AGENT_ROOT / "schemas" / "runtime-policy.schema.json").read_text())
TOOL_CALL_SCHEMA = json.loads((REPO_ROOT / "shared" / "schemas" / "tool-call.schema.json").read_text())


def sealed(value):
    value["content_sha256"] = digest(value, omit=("content_sha256",))
    return value


def policy_document(**budget_overrides):
    budgets = {"tool_calls": 20, "wall_time_seconds": 5000, "experiments": 3,
               "patch_files": 2, "patch_lines": 100, "output_bytes": 2_000_000}
    budgets.update(budget_overrides)
    return sealed({
        "schema_version": 1, "id": "POL-RUN-1", "run_id": "RUN-1", "network": "denied",
        "readable_paths": ["app", "benchmark", "tests"], "writable_paths": ["app/src"],
        "protected_paths": ["benchmark", "tests"],
        "allowed_executables": {"build_variant": ["./gradlew", "sh"], "run_benchmark": ["./gradlew"]},
        "allowed_working_directories": ["."], "allowed_environment_keys": ["JAVA_HOME"],
        "device_serial_hash": SHA, "package_name": "com.example.app",
        "allowed_partitions": ["DEVELOPMENT"], "task_approved_risks": ["R0", "R1"],
        "allow_external_publication": False, "budgets": budgets,
    })


def patch_arguments(*, approval_id="AP-PATCH", changed_paths=None, changed_line_count=20):
    return {"intervention_id": "IP-ONE", "patch_artifact_id": "AR-PATCH", "patch_sha256": SHA,
            "baseline_source_sha256": SHA, "allowed_paths": ["app/src"],
            "changed_paths": changed_paths or ["app/src/Main.kt"],
            "changed_line_count": changed_line_count, "approval_id": approval_id}


def approval_for(request):
    return sealed({"schema_version": 1, "id": request.arguments["approval_id"], "run_id": "RUN-1",
                   "risk": "R2", "scope": {"intervention_id": "IP-ONE"},
                   "request_sha256": request.request_sha256, "decision": "APPROVED",
                   "decided_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z",
                   "approver_ref": "human:test"})


def build_request(**changes):
    arguments = {"executable": "./gradlew", "args": ["assembleRelease"], "working_directory": ".",
                 "environment": {"JAVA_HOME": "/jdk"}, "timeout_seconds": 120,
                 "output_limit_bytes": 100_000}
    arguments.update(changes)
    return ToolRequest("build_variant", arguments, "2026-01-01T00:00:10Z")


class PolicyEngineTest(unittest.TestCase):
    def snapshot(self):
        from causalperf_agent.execution import ExecutionSnapshot
        return ExecutionSnapshot("RUN-1")

    def test_task_approved_build_reserves_budget(self):
        snapshot = self.snapshot(); engine = PolicyEngine(RuntimePolicy(policy_document()))
        decision = engine.authorize_and_reserve(build_request(), snapshot)
        self.assertEqual(decision.status, "ALLOW")
        self.assertEqual(snapshot.budget_usage["tool_calls"], 1)
        self.assertEqual(snapshot.budget_usage["wall_time_seconds"], 120)
        self.assertEqual(snapshot.policy_sha256, engine.policy.digest)

    def test_runtime_policy_matches_published_schema(self):
        jsonschema.validate(policy_document(), RUNTIME_POLICY_SCHEMA)

    def test_tool_request_arguments_are_immutable_copies(self):
        arguments = {"paths": ["app/src/Main.kt"]}
        request = ToolRequest("inspect_source", arguments, AUTHORIZATION_TIME)
        original_digest = request.request_sha256
        arguments["paths"].append("tests/Hidden.kt")
        returned = request.arguments
        returned["paths"].append("benchmark/Hidden.kt")
        self.assertEqual(request.arguments, {"paths": ["app/src/Main.kt"]})
        self.assertEqual(request.request_sha256, original_digest)

    def test_policy_rejects_overlapping_writable_and_protected_paths(self):
        value = policy_document(); value["protected_paths"].append("app")
        sealed(value)
        with self.assertRaisesRegex(ValueError, "paths overlap"):
            RuntimePolicy(value)

    def test_unknown_tool_fails_closed(self):
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        decision = engine.evaluate(ToolRequest("delete_workspace", {}, "2026-01-01T00:00:10Z"), self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("UNKNOWN_TOOL", decision.reason_codes)

    def test_shell_executable_is_denied_even_when_listed(self):
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        decision = engine.evaluate(build_request(executable="sh", args=["-c", "./gradlew assemble"]), self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("SHELL_EXECUTABLE_FORBIDDEN", decision.reason_codes)

    def test_command_substitution_is_denied(self):
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        decision = engine.evaluate(build_request(args=["$(curl bad.example)"]), self.snapshot())
        self.assertIn("UNSAFE_COMMAND_ARGUMENT", decision.reason_codes)

    def test_protected_path_mutation_is_denied_before_approval(self):
        request = ToolRequest("apply_patch", patch_arguments(changed_paths=["benchmark/StartupBenchmark.kt"]), "2026-01-01T00:00:10Z")
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        decision = engine.evaluate(request, self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("PROTECTED_PATH_MUTATION", decision.reason_codes)

    def test_path_traversal_cannot_escape_writable_scope(self):
        request = ToolRequest(
            "apply_patch",
            patch_arguments(changed_paths=["app/src/../../tests/CorrectnessTest.kt"]),
            AUTHORIZATION_TIME,
        )
        decision = PolicyEngine(RuntimePolicy(policy_document())).evaluate(request, self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("MALFORMED_TOOL_REQUEST", decision.reason_codes)

    def test_r2_requires_exact_active_approval(self):
        request = ToolRequest("apply_patch", patch_arguments(), "2026-01-01T00:00:10Z")
        without = PolicyEngine(RuntimePolicy(policy_document())).evaluate(request, self.snapshot())
        self.assertEqual(without.status, "REQUIRE_APPROVAL")
        self.assertIn("APPROVAL_REQUIRED", without.reason_codes)
        engine = PolicyEngine(
            RuntimePolicy(policy_document()), [approval_for(request)],
            clock=lambda: AUTHORIZATION_TIME,
        )
        snapshot = self.snapshot(); allowed = engine.authorize_and_reserve(request, snapshot)
        self.assertEqual(allowed.status, "ALLOW")
        self.assertEqual(snapshot.rollback_obligations, ["IP-ONE"])

    def test_r2_request_without_approval_id_is_denied(self):
        request = ToolRequest("apply_patch", patch_arguments(approval_id=None), "2026-01-01T00:00:10Z")
        decision = PolicyEngine(RuntimePolicy(policy_document())).evaluate(request, self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("MALFORMED_TOOL_REQUEST", decision.reason_codes)

    def test_changed_request_invalidates_approval(self):
        original = ToolRequest("apply_patch", patch_arguments(), "2026-01-01T00:00:10Z")
        changed = ToolRequest("apply_patch", patch_arguments(changed_line_count=21), "2026-01-01T00:00:10Z")
        decision = PolicyEngine(RuntimePolicy(policy_document()), [approval_for(original)]).evaluate(changed, self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("APPROVAL_REQUEST_MISMATCH", decision.reason_codes)

    def test_malformed_approval_is_denied_without_exception(self):
        request = ToolRequest(
            "apply_patch", patch_arguments(), AUTHORIZATION_TIME,
            authorization_at=AUTHORIZATION_TIME,
        )
        approval = approval_for(request)
        del approval["scope"]
        sealed(approval)
        decision = PolicyEngine(RuntimePolicy(policy_document()), [approval]).evaluate(request, self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("APPROVAL_RECORD_INVALID", decision.reason_codes)

    def test_approval_may_follow_request_but_must_precede_authorization(self):
        request = ToolRequest("apply_patch", patch_arguments(), "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z")
        approval = approval_for(request); approval["decided_at"] = "2026-01-01T00:00:05Z"; sealed(approval)
        decision = PolicyEngine(RuntimePolicy(policy_document()), [approval]).evaluate(request, self.snapshot())
        self.assertEqual(decision.status, "ALLOW")

    def test_budget_cannot_be_extended_by_request(self):
        engine = PolicyEngine(RuntimePolicy(policy_document(wall_time_seconds=100)))
        decision = engine.evaluate(build_request(timeout_seconds=101), self.snapshot())
        self.assertEqual(decision.status, "DENY")
        self.assertIn("BUDGET_EXCEEDED:wall_time_seconds", decision.reason_codes)

    def test_device_and_package_are_exactly_scoped(self):
        request = ToolRequest("install_apk", {"device_serial_hash": "b" * 64, "package_name": "com.other.app",
            "apk_artifact_id": "AR-APK", "apk_sha256": SHA, "timeout_seconds": 30}, "2026-01-01T00:00:10Z")
        decision = PolicyEngine(RuntimePolicy(policy_document())).evaluate(request, self.snapshot())
        self.assertIn("DEVICE_NOT_ALLOWED", decision.reason_codes)
        self.assertIn("PACKAGE_NOT_ALLOWED", decision.reason_codes)

    def test_rollback_is_budget_exempt_but_requires_obligation(self):
        snapshot = self.snapshot(); snapshot.rollback_obligations = ["IP-ONE"]
        snapshot.budget_usage = {"tool_calls": 20, "wall_time_seconds": 5000, "experiments": 3,
                                 "patch_files": 2, "patch_lines": 100, "output_bytes": 2_000_000}
        request = ToolRequest("rollback_patch", {"intervention_id": "IP-ONE", "strategy": "reverse_patch",
            "expected_source_sha256": SHA, "approval_id": "AP-PATCH"}, "2026-01-01T00:00:10Z")
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        self.assertEqual(engine.authorize_and_reserve(request, snapshot).status, "ALLOW")
        self.assertEqual(snapshot.budget_usage["tool_calls"], 20)
        engine.complete_rollback(snapshot, "IP-ONE")
        self.assertEqual(snapshot.rollback_obligations, [])


class GuardedControllerTest(unittest.TestCase):
    def test_policy_denial_prevents_adapter_side_effect(self):
        delegate = SimulatedAdapter()
        engine = PolicyEngine(RuntimePolicy(policy_document()))
        request = ToolRequest("apply_patch", patch_arguments(changed_paths=["tests/CorrectnessTest.kt"]), "2026-01-01T00:00:10Z")
        guarded = GuardedExecutionAdapter(delegate, engine, lambda state, snapshot: request if state == ExecutionState.APPLYING_INTERVENTION else None)
        result = ExperimentController("RUN-1", guarded).run()
        self.assertEqual(result.state, ExecutionState.FAILED)
        self.assertNotIn(ExecutionState.APPLYING_INTERVENTION, delegate.executed)
        self.assertEqual(delegate.workspace, "BASELINE")
        jsonschema.validate(guarded.audit_records[0], TOOL_CALL_SCHEMA)
        self.assertEqual(guarded.audit_records[0]["status"], "DENIED")

    def test_missing_approval_emits_contract_valid_pending_record(self):
        request = ToolRequest("apply_patch", patch_arguments(), AUTHORIZATION_TIME)
        guarded = GuardedExecutionAdapter(
            SimulatedAdapter(), PolicyEngine(RuntimePolicy(policy_document())),
            lambda state, snapshot: request,
        )
        result = guarded.authorize(ExecutionState.APPLYING_INTERVENTION, ExecutionSnapshot("RUN-1"))
        self.assertEqual(result.status, "INCONCLUSIVE")
        jsonschema.validate(guarded.audit_records[0], TOOL_CALL_SCHEMA)
        self.assertEqual(guarded.audit_records[0]["status"], "APPROVAL_PENDING")

    def test_approved_patch_audit_binds_exact_approval(self):
        request = ToolRequest("apply_patch", patch_arguments(), AUTHORIZATION_TIME)
        approval = approval_for(request)
        guarded = GuardedExecutionAdapter(
            SimulatedAdapter(),
            PolicyEngine(
                RuntimePolicy(policy_document()), [approval],
                clock=lambda: AUTHORIZATION_TIME,
            ),
            lambda state, snapshot: request,
        )
        snapshot = ExecutionSnapshot("RUN-1")
        self.assertEqual(guarded.authorize(ExecutionState.APPLYING_INTERVENTION, snapshot).status, "PASS")
        self.assertEqual(guarded.execute(ExecutionState.APPLYING_INTERVENTION, snapshot).status, "PASS")
        jsonschema.validate(guarded.audit_records[0], TOOL_CALL_SCHEMA)
        verify_tool_approval(guarded.audit_records[0], [approval])

    def test_successful_tool_call_emits_contract_valid_audit_record(self):
        delegate = SimulatedAdapter()
        guarded = GuardedExecutionAdapter(
            delegate,
            PolicyEngine(RuntimePolicy(policy_document())),
            lambda state, snapshot: build_request() if state == ExecutionState.BUILDING_BASELINE else None,
        )
        snapshot = ExecutionSnapshot("RUN-1")
        self.assertEqual(guarded.authorize(ExecutionState.BUILDING_BASELINE, snapshot).status, "PASS")
        self.assertEqual(guarded.execute(ExecutionState.BUILDING_BASELINE, snapshot).status, "PASS")
        jsonschema.validate(guarded.audit_records[0], TOOL_CALL_SCHEMA)
        self.assertEqual(guarded.audit_records[0]["status"], "SUCCEEDED")
        self.assertEqual(guarded.audit_records[0]["exit_code"], 0)

    def test_crash_checkpoint_preserves_obligation_and_recovery_clears_it(self):
        request = ToolRequest("apply_patch", patch_arguments(), "2026-01-01T00:00:10Z")
        approval = approval_for(request)
        delegate = SimulatedAdapter(crash_after_mutation_at={ExecutionState.APPLYING_INTERVENTION})
        engine = PolicyEngine(
            RuntimePolicy(policy_document()), [approval],
            clock=lambda: AUTHORIZATION_TIME,
        )
        guarded = GuardedExecutionAdapter(delegate, engine, lambda state, snapshot: request if state == ExecutionState.APPLYING_INTERVENTION else None)
        with tempfile.TemporaryDirectory() as directory:
            store = FileRunStore(Path(directory) / "RUN-1")
            audit_store = FileToolCallAuditStore(Path(directory) / "RUN-1" / "tool-calls.json")
            guarded.audit_store = audit_store
            controller = ExperimentController("RUN-1", guarded, store=store)
            with self.assertRaises(InjectedCrash):
                controller.run()
            persisted, _ = store.load()
            self.assertEqual(persisted["rollback_obligations"], ["IP-ONE"])
            persisted_calls = audit_store.load()
            self.assertEqual(len(persisted_calls), 1)
            self.assertEqual(persisted_calls[0]["status"], "RUNNING")
            delegate.crash_after_mutation_at.clear()
            restored_guard = GuardedExecutionAdapter(
                delegate,
                PolicyEngine(
                    RuntimePolicy(policy_document()), [approval],
                    clock=lambda: AUTHORIZATION_TIME,
                ),
                guarded.request_factory,
                audit_store=audit_store,
            )
            self.assertEqual(restored_guard.audit_records[0]["status"], "FAILED")
            self.assertEqual(
                restored_guard.audit_records[0]["failure_code"],
                "INTERRUPTED_BEFORE_COMPLETION",
            )
            restored = ExperimentController.restore_from_store(store, restored_guard)
            result = restored.recover()
            self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
            self.assertEqual(result.rollback_obligations, [])
            self.assertEqual(delegate.workspace, "BASELINE")


if __name__ == "__main__":
    unittest.main()
