from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import (
    GradleBenchmarkExecutionAdapter,
    GradleBenchmarkRequest,
    GradleBenchmarkRunner,
    ProcessOutput,
    digest_tree,
)
from causalperf_agent.execution import ExecutionSnapshot, ExecutionState, SimulatedAdapter
from causalperf_agent.policy import GuardedExecutionAdapter, PolicyEngine, RuntimePolicy
from causalperf_reference.artifacts import digest, verify_content_digest


ROOT = Path(__file__).parents[2]
ARTIFACT_SCHEMA = json.loads((ROOT / "shared/schemas/artifact.schema.json").read_text())
MEASUREMENT_SCHEMA = json.loads((ROOT / "shared/schemas/measurement-set.schema.json").read_text())
TOOL_SCHEMA = json.loads((ROOT / "causalperf-agent/schemas/tool-contract.schema.json").read_text())
SERIAL = "LAB-DEVICE-001"


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        result = f"2026-08-30T01:00:{self.value:02d}Z"
        self.value += 1
        return result


class EffectTransport:
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


class GradleBenchmarkRunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "app/src/main"
        self.source.mkdir(parents=True)
        (self.source / "Main.kt").write_text("class Main\n")
        (self.root / "gradlew").write_text("wrapper\n")
        self.apk = self.root / "app/build/outputs/apk/benchmark/app.apk"
        self.apk.parent.mkdir(parents=True)
        self.apk.write_bytes(b"sealed-apk")
        self.results = self.root / "macrobenchmark/build/outputs/connected_android_test_additional_output/benchmark/connected/device"

    def request(self, **overrides):
        value = {
            "run_id": "RUN-CPU-001-CAL-001",
            "arm": "A1",
            "task_root": self.root,
            "wrapper_relative_path": "gradlew",
            "args": (":macrobenchmark:connectedCheck",),
            "environment": {"JAVA_HOME": "/jdk"},
            "timeout_seconds": 7200,
            "output_limit_bytes": 1_000_000,
            "result_limit_bytes": 10_000_000,
            "result_file_limit": 20,
            "source_relative_path": "app/src/main",
            "source_sha256": digest_tree(self.source),
            "apk_relative_path": "app/build/outputs/apk/benchmark/app.apk",
            "apk_artifact_id": "AR-APK-CPU-001-A1",
            "apk_sha256": hashlib.sha256(b"sealed-apk").hexdigest(),
            "result_root_relative_path": "macrobenchmark/build/outputs/connected_android_test_additional_output/benchmark/connected/device",
            "device_serial": SERIAL,
            "device_serial_hash": digest(SERIAL),
            "package_name": "dev.causalperf.startup.cpu",
            "partition": "CALIBRATION",
            "sequence_plan_sha256": "b" * 64,
            "environment_snapshot_id": "ENV-CPU-001-A1",
            "metric": "timeToInitialDisplayMs",
            "unit": "ms",
            "expected_iterations": 3,
            "warmup_count": 3,
            "stabilization_evidence_sha256": "c" * 64,
            "max_invalid_percent": 10,
            "predeclared_exclusion_codes": ("THERMAL", "DEVICE_DISCONNECT"),
            "benchmark_name": "coldStartup",
            "benchmark_class": "dev.causalperf.startup.cpu.macrobenchmark.ColdStartupBenchmark",
        }
        value.update(overrides)
        return GradleBenchmarkRequest(**value)

    def write_outputs(self, _spec, *, values=(101.5, 99.0, 103.25)):
        self.results.mkdir(parents=True, exist_ok=True)
        document = {
            "context": {"build": {"device": "redacted"}},
            "benchmarks": [{
                "name": "coldStartup",
                "className": "dev.causalperf.startup.cpu.macrobenchmark.ColdStartupBenchmark",
                "metrics": {"timeToInitialDisplayMs": {"runs": list(values)}},
                "repeatIterations": len(values),
            }],
        }
        (self.results / "macrobenchmark-benchmarkData.json").write_text(json.dumps(document))
        for index in range(len(values)):
            (self.results / f"ColdStartupBenchmark_coldStartup_iter{index:03d}.perfetto-trace").write_bytes(
                f"trace-{index}".encode()
            )

    def test_collects_json_and_one_trace_per_measurement(self):
        transport = EffectTransport(ProcessOutput(0, b"BUILD SUCCESSFUL"), self.write_outputs)
        attempt = GradleBenchmarkRunner(transport, clock=Clock()).run(self.request())

        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(len(attempt.artifacts), 4)
        for artifact in attempt.artifacts:
            jsonschema.Draft202012Validator(ARTIFACT_SCHEMA).validate(artifact)
            self.assertEqual(artifact["retention"], "PERMANENT")
        jsonschema.Draft202012Validator(MEASUREMENT_SCHEMA).validate(attempt.measurement_set)
        verify_content_digest(attempt.measurement_set)
        self.assertEqual(
            [item["value"] for item in attempt.measurement_set["measurements"]],
            [101.5, 99.0, 103.25],
        )
        self.assertEqual(
            len({item["trace_sha256"] for item in attempt.measurement_set["measurements"]}),
            3,
        )
        self.assertNotIn(SERIAL, json.dumps(attempt.result))
        self.assertEqual(transport.calls[0].argv[1:], (":macrobenchmark:connectedCheck",))
        self.assertEqual(transport.calls[0].environment["ANDROID_SERIAL"], SERIAL)

    def test_tool_request_is_frozen_and_contains_no_raw_serial(self):
        request = self.request()
        tool = request.tool_request("2026-08-30T00:00:00Z")
        document = {"schema_version": 1, "tool_id": tool.tool_id, "request": tool.arguments}
        jsonschema.Draft202012Validator(TOOL_SCHEMA).validate(document)
        self.assertNotIn(SERIAL, json.dumps(document))

    def test_stale_results_are_removed_before_execution(self):
        self.results.mkdir(parents=True)
        (self.results / "stale-benchmarkData.json").write_text("not json")
        attempt = GradleBenchmarkRunner(
            EffectTransport(ProcessOutput(0), self.write_outputs), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "PASS")

    def test_missing_trace_keeps_raw_result_but_is_inconclusive(self):
        def incomplete(spec):
            self.write_outputs(spec)
            next(self.results.glob("*_iter001.perfetto-trace")).unlink()

        attempt = GradleBenchmarkRunner(
            EffectTransport(ProcessOutput(0), incomplete), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("MACROBENCHMARK_TRACE_SET_INCOMPLETE", attempt.reason_codes)
        self.assertIsNone(attempt.measurement_set)
        self.assertEqual(len(attempt.artifacts), 3)

    def test_iteration_mismatch_is_inconclusive(self):
        attempt = GradleBenchmarkRunner(
            EffectTransport(ProcessOutput(0), lambda spec: self.write_outputs(spec, values=(1, 2))),
            clock=Clock(),
        ).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("MACROBENCHMARK_ITERATION_COUNT_MISMATCH", attempt.reason_codes)

    def test_source_or_apk_drift_is_rejected(self):
        request = self.request()
        self.apk.write_bytes(b"changed")
        transport = EffectTransport(ProcessOutput(0), self.write_outputs)
        with self.assertRaisesRegex(ValueError, "APK identity changed"):
            GradleBenchmarkRunner(transport, clock=Clock()).run(request)
        self.assertEqual(transport.calls, [])

    def test_nonzero_process_and_output_limits_are_inconclusive(self):
        attempt = GradleBenchmarkRunner(
            EffectTransport(ProcessOutput(1, stderr=b"failed")), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("MACROBENCHMARK_PROCESS_FAILED", attempt.reason_codes)

        attempt = GradleBenchmarkRunner(
            EffectTransport(ProcessOutput(0), self.write_outputs), clock=Clock()
        ).run(self.request(result_limit_bytes=5))
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("MACROBENCHMARK_RESULT_LIMIT_EXCEEDED", attempt.reason_codes)

    def test_stabilization_evidence_is_mandatory(self):
        with self.assertRaisesRegex(ValueError, "stabilization evidence"):
            self.request(stabilization_evidence_sha256=None)

    def test_windows_wrapper_is_one_exact_argument(self):
        (self.root / "gradlew.bat").write_text("@echo off\r\n")
        transport = EffectTransport(ProcessOutput(0), self.write_outputs)
        GradleBenchmarkRunner(transport, clock=Clock()).run(
            self.request(wrapper_relative_path="gradlew.bat")
        )
        self.assertEqual(transport.calls[0].argv[0], str((self.root / "gradlew.bat").resolve()))

    def policy(self):
        return RuntimePolicy(sealed({
            "schema_version": 1,
            "id": "POL-CPU-001-CAL",
            "run_id": "RUN-CPU-001-CAL-001",
            "network": "denied",
            "readable_paths": ["app"],
            "writable_paths": ["app/src/main"],
            "protected_paths": ["app/src/androidTest", "macrobenchmark"],
            "allowed_executables": {"build_variant": ["./gradlew"], "run_benchmark": ["./gradlew"]},
            "allowed_working_directories": ["."],
            "allowed_environment_keys": ["JAVA_HOME"],
            "device_serial_hash": digest(SERIAL),
            "package_name": "dev.causalperf.startup.cpu",
            "allowed_partitions": ["CALIBRATION"],
            "task_approved_risks": ["R0", "R1"],
            "allow_external_publication": False,
            "budgets": {"tool_calls": 10, "wall_time_seconds": 30000, "experiments": 5,
                        "patch_files": 4, "patch_lines": 100, "output_bytes": 10_000_000},
        }))

    def test_execution_adapter_runs_only_after_policy_authorization(self):
        request = self.request()
        transport = EffectTransport(ProcessOutput(0), self.write_outputs)
        adapter = GradleBenchmarkExecutionAdapter(
            SimulatedAdapter(), GradleBenchmarkRunner(transport, clock=Clock()),
            {ExecutionState.MEASURING_A1: request},
        )
        guarded = GuardedExecutionAdapter(
            adapter,
            PolicyEngine(self.policy(), clock=lambda: "2026-08-30T00:00:00Z"),
            lambda state, snapshot: request.tool_request("2026-08-30T00:00:00Z")
            if state == ExecutionState.MEASURING_A1 else None,
        )
        snapshot = ExecutionSnapshot(request.run_id)
        self.assertEqual(guarded.authorize(ExecutionState.MEASURING_A1, snapshot).status, "PASS")
        result = guarded.execute(ExecutionState.MEASURING_A1, snapshot)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(transport.calls), 1)

    def test_wrong_arm_is_rejected_before_transport(self):
        request = self.request(arm="B")
        transport = EffectTransport(ProcessOutput(0), self.write_outputs)
        adapter = GradleBenchmarkExecutionAdapter(
            SimulatedAdapter(), GradleBenchmarkRunner(transport, clock=Clock()),
            {ExecutionState.MEASURING_A1: request},
        )
        result = adapter.execute(ExecutionState.MEASURING_A1, ExecutionSnapshot(request.run_id))
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
