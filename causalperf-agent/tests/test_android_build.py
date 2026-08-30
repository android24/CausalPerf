from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import (
    GradleBuildAdapter,
    GradleBuildExecutionAdapter,
    GradleBuildRequest,
    ProcessOutput,
)
from causalperf_agent.execution import ExecutionSnapshot, ExecutionState, SimulatedAdapter
from causalperf_agent.policy import GuardedExecutionAdapter, PolicyEngine, RuntimePolicy
from causalperf_reference.artifacts import digest, verify_content_digest


ROOT = Path(__file__).parents[2]
BUILD_SCHEMA = json.loads((ROOT / "shared" / "schemas" / "build-result.schema.json").read_text())
ARTIFACT_SCHEMA = json.loads((ROOT / "shared" / "schemas" / "artifact.schema.json").read_text())


class Clock:
    def __init__(self):
        self.second = 0

    def __call__(self):
        value = f"2026-08-29T00:00:{self.second:02d}Z"
        self.second += 1
        return value


class FakeTransport:
    def __init__(self, output, effect=None):
        self.output = output
        self.effect = effect
        self.calls = []

    def run(self, spec):
        self.calls.append(spec)
        if self.effect:
            self.effect(spec)
        return self.output


def sealed(value):
    value["content_sha256"] = digest(value, omit=("content_sha256",))
    return value


class GradleBuildAdapterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.task = Path(self.directory.name) / "public-task"
        (self.task / "app" / "src" / "main").mkdir(parents=True)
        (self.task / "app" / "src" / "main" / "Main.kt").write_text("class Main\n")
        (self.task / "gradlew").write_text("wrapper\n")
        self.apk = self.task / "app" / "build" / "outputs" / "apk" / "benchmark" / "app-benchmark.apk"

    def request(self, **overrides):
        value = {
            "run_id": "RUN-CPU-001-DRY-001",
            "role": "BASELINE",
            "task_root": self.task,
            "wrapper_relative_path": "gradlew",
            "args": ("clean", ":app:assembleBenchmark"),
            "environment": {"JAVA_HOME": "/jdk"},
            "timeout_seconds": 1200,
            "output_limit_bytes": 100_000,
            "source_relative_path": "app/src/main",
            "apk_relative_path": "app/build/outputs/apk/benchmark/app-benchmark.apk",
            "source_artifact_id": "AR-SOURCE-BASELINE",
            "toolchain": {"gradle": "9.5.0", "agp": "9.3.0", "java": "17"},
        }
        value.update(overrides)
        return GradleBuildRequest(**value)

    def create_apk(self, _spec):
        self.apk.parent.mkdir(parents=True, exist_ok=True)
        self.apk.write_bytes(b"deterministic-apk")

    def test_success_emits_schema_valid_build_and_apk_artifacts(self):
        transport = FakeTransport(ProcessOutput(0, b"BUILD SUCCESSFUL\n"), self.create_apk)
        attempt = GradleBuildAdapter(transport, clock=Clock()).build(self.request())

        self.assertEqual(attempt.status, "PASS")
        jsonschema.Draft202012Validator(BUILD_SCHEMA).validate(attempt.build_result)
        jsonschema.Draft202012Validator(ARTIFACT_SCHEMA).validate(attempt.apk_artifact)
        verify_content_digest(attempt.build_result)
        self.assertEqual(transport.calls[0].argv[1:], ("clean", ":app:assembleBenchmark"))
        self.assertEqual(transport.calls[0].working_directory, self.task.resolve())
        self.assertEqual(attempt.build_result["output_artifact_ids"], [attempt.apk_artifact["id"]])

    def test_nonzero_gradle_exit_fails_without_apk_claim(self):
        attempt = GradleBuildAdapter(
            FakeTransport(ProcessOutput(1, stderr=b"compile failed")), clock=Clock()
        ).build(self.request())
        self.assertEqual(attempt.status, "FAIL")
        self.assertIsNone(attempt.apk_artifact)
        self.assertEqual(attempt.build_result["reason_codes"], ["GRADLE_BUILD_FAILED"])

    def test_timeout_is_inconclusive(self):
        attempt = GradleBuildAdapter(
            FakeTransport(ProcessOutput(None, timed_out=True)), clock=Clock()
        ).build(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("GRADLE_TIMEOUT", attempt.build_result["reason_codes"])

    def test_success_without_declared_apk_is_inconclusive(self):
        attempt = GradleBuildAdapter(
            FakeTransport(ProcessOutput(0, b"BUILD SUCCESSFUL")), clock=Clock()
        ).build(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("APK_NOT_PRODUCED", attempt.build_result["reason_codes"])

    def test_stale_apk_is_removed_before_current_build(self):
        self.create_apk(None)
        attempt = GradleBuildAdapter(
            FakeTransport(ProcessOutput(0, b"BUILD SUCCESSFUL")), clock=Clock()
        ).build(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertFalse(self.apk.exists())
        self.assertIn("APK_NOT_PRODUCED", attempt.build_result["reason_codes"])

    def test_source_mutation_during_build_fails(self):
        def mutate(spec):
            self.create_apk(spec)
            (self.task / "app" / "src" / "main" / "Main.kt").write_text("changed\n")

        attempt = GradleBuildAdapter(
            FakeTransport(ProcessOutput(0), mutate), clock=Clock()
        ).build(self.request())
        self.assertEqual(attempt.status, "FAIL")
        self.assertIn("SOURCE_CHANGED_DURING_BUILD", attempt.build_result["reason_codes"])

    def test_reproducibility_build_requires_clean_first(self):
        with self.assertRaisesRegex(ValueError, "must begin with clean"):
            self.request(args=(":app:assembleBenchmark",))

    def test_task_relative_paths_cannot_escape(self):
        request = self.request(apk_relative_path="../outside.apk")
        with self.assertRaisesRegex(ValueError, "unsafe APK path"):
            GradleBuildAdapter(FakeTransport(ProcessOutput(0)), clock=Clock()).build(request)

    def test_source_tree_symlink_is_rejected(self):
        link = self.task / "app" / "src" / "main" / "linked"
        try:
            link.symlink_to(self.task / "app" / "src" / "main" / "Main.kt")
        except OSError:
            self.skipTest("filesystem does not support symlinks")
        with self.assertRaisesRegex(ValueError, "source symlink forbidden"):
            GradleBuildAdapter(FakeTransport(ProcessOutput(0)), clock=Clock()).build(
                self.request()
            )

    def test_windows_wrapper_is_exactly_represented_without_shell(self):
        (self.task / "gradlew.bat").write_text("@echo off\r\n")
        request = self.request(wrapper_relative_path="gradlew.bat")
        self.assertEqual(request.command()["executable"], "./gradlew.bat")
        self.assertEqual(request.wrapper_path, (self.task / "gradlew.bat").resolve())

    def test_request_configuration_is_immutable(self):
        environment = {"JAVA_HOME": "/jdk"}
        request = self.request(environment=environment)
        environment["SECRET"] = "changed-after-seal"
        self.assertEqual(dict(request.environment), {"JAVA_HOME": "/jdk"})
        with self.assertRaises(TypeError):
            request.environment["SECRET"] = "no"

    def policy(self):
        return RuntimePolicy(sealed({
            "schema_version": 1,
            "id": "POL-CPU-001-DRY",
            "run_id": "RUN-CPU-001-DRY-001",
            "network": "denied",
            "readable_paths": ["app"],
            "writable_paths": ["app/src/main"],
            "protected_paths": ["app/src/androidTest", "macrobenchmark"],
            "allowed_executables": {
                "build_variant": ["./gradlew"],
                "run_benchmark": ["./gradlew"],
            },
            "allowed_working_directories": ["."],
            "allowed_environment_keys": ["JAVA_HOME"],
            "device_serial_hash": "a" * 64,
            "package_name": "dev.causalperf.startup.cpu",
            "allowed_partitions": ["DEVELOPMENT"],
            "task_approved_risks": ["R0", "R1"],
            "allow_external_publication": False,
            "budgets": {
                "tool_calls": 10,
                "wall_time_seconds": 5000,
                "experiments": 2,
                "patch_files": 4,
                "patch_lines": 100,
                "output_bytes": 1_000_000,
            },
        }))

    def test_build_executes_through_guarded_boundary(self):
        request = self.request()
        transport = FakeTransport(ProcessOutput(0), self.create_apk)
        adapter = GradleBuildExecutionAdapter(
            SimulatedAdapter(),
            GradleBuildAdapter(transport, clock=Clock()),
            {ExecutionState.BUILDING_BASELINE: request},
        )
        guarded = GuardedExecutionAdapter(
            adapter,
            PolicyEngine(self.policy(), clock=lambda: "2026-08-29T00:00:00Z"),
            lambda state, snapshot: request.tool_request("2026-08-29T00:00:00Z")
            if state == ExecutionState.BUILDING_BASELINE else None,
        )
        snapshot = ExecutionSnapshot(request.run_id)

        self.assertEqual(guarded.authorize(ExecutionState.BUILDING_BASELINE, snapshot).status, "PASS")
        result = guarded.execute(ExecutionState.BUILDING_BASELINE, snapshot)

        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(guarded.audit_records[0]["status"], "SUCCEEDED")

    def test_policy_denial_prevents_gradle_transport(self):
        request = self.request(environment={"UNDECLARED_SECRET": "value"})
        transport = FakeTransport(ProcessOutput(0), self.create_apk)
        adapter = GradleBuildExecutionAdapter(
            SimulatedAdapter(), GradleBuildAdapter(transport, clock=Clock()),
            {ExecutionState.BUILDING_BASELINE: request},
        )
        guarded = GuardedExecutionAdapter(
            adapter, PolicyEngine(self.policy()),
            lambda state, snapshot: request.tool_request("2026-08-29T00:00:00Z"),
        )

        result = guarded.authorize(
            ExecutionState.BUILDING_BASELINE, ExecutionSnapshot(request.run_id)
        )
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
