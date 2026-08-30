from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import (
    CorrectnessExecutionAdapter,
    GradleCorrectnessRequest,
    GradleCorrectnessRunner,
    ProcessOutput,
    digest_tree,
)
from causalperf_agent.execution import ExecutionSnapshot, ExecutionState, SimulatedAdapter
from causalperf_reference.artifacts import digest, verify_content_digest


ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "shared/schemas/correctness-report.schema.json").read_text())
SERIAL = "LAB-DEVICE-001"


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        result = f"2026-08-30T00:00:{self.value:02d}Z"
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


class GradleCorrectnessRunnerTest(unittest.TestCase):
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
        self.results = self.root / "app/build/outputs/androidTest-results/connected/benchmark"

    def request(self, **overrides):
        value = {
            "run_id": "RUN-CPU-001-DRY-001", "phase": "BASELINE",
            "task_root": self.root, "wrapper_relative_path": "gradlew",
            "args": (":app:connectedBenchmarkAndroidTest",),
            "environment": {"JAVA_HOME": "/jdk", "ANDROID_SERIAL": SERIAL},
            "timeout_seconds": 1800, "output_limit_bytes": 1_000_000,
            "device_serial": SERIAL, "device_serial_hash": digest(SERIAL),
            "source_relative_path": "app/src/main",
            "source_manifest_id": "SM-CPU-001-BASELINE",
            "source_sha256": digest_tree(self.source),
            "apk_relative_path": "app/build/outputs/apk/benchmark/app.apk",
            "apk_sha256": hashlib.sha256(b"sealed-apk").hexdigest(),
            "suite_id": "cpu-001-public-correctness-v1", "suite_sha256": "a" * 64,
            "result_root_relative_path": "app/build/outputs/androidTest-results/connected/benchmark",
        }
        value.update(overrides)
        return GradleCorrectnessRequest(**value)

    def write_pass(self, _spec):
        self.results.mkdir(parents=True, exist_ok=True)
        (self.results / "TEST-public.xml").write_bytes(
            b"<testsuite><testcase name='passes'/></testsuite>"
        )

    def test_runs_sealed_command_and_emits_schema_valid_report(self):
        transport = EffectTransport(ProcessOutput(0, b"BUILD SUCCESSFUL"), self.write_pass)
        attempt = GradleCorrectnessRunner(transport, clock=Clock()).run(self.request())
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(transport.calls[0].argv[1:], (":app:connectedBenchmarkAndroidTest",))
        self.assertEqual(transport.calls[0].environment["ANDROID_SERIAL"], SERIAL)
        jsonschema.Draft202012Validator(SCHEMA).validate(attempt.report)
        verify_content_digest(attempt.report)

    def test_stale_junit_results_are_removed_before_execution(self):
        self.results.mkdir(parents=True)
        (self.results / "TEST-stale.xml").write_bytes(
            b"<testsuite><testcase name='stale'><failure/></testcase></testsuite>"
        )
        attempt = GradleCorrectnessRunner(
            EffectTransport(ProcessOutput(0), self.write_pass), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(attempt.report["test_count"], 1)
        self.assertEqual(attempt.report["failure_count"], 0)

    def test_no_junit_output_is_inconclusive(self):
        attempt = GradleCorrectnessRunner(
            EffectTransport(ProcessOutput(0)), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("CORRECTNESS_ZERO_TESTS", attempt.reason_codes)

    def test_source_or_apk_mutation_fails_correctness(self):
        def mutate_source(spec):
            self.write_pass(spec)
            (self.source / "Main.kt").write_text("changed\n")

        source_attempt = GradleCorrectnessRunner(
            EffectTransport(ProcessOutput(0), mutate_source), clock=Clock()
        ).run(self.request())
        self.assertEqual(source_attempt.status, "FAIL")
        self.assertIn("SOURCE_CHANGED_DURING_CORRECTNESS", source_attempt.reason_codes)

    def test_result_byte_limit_is_fail_closed(self):
        def oversized(spec):
            self.results.mkdir(parents=True)
            (self.results / "TEST-large.xml").write_bytes(b"<testsuite>" + b"x" * 100 + b"</testsuite>")

        attempt = GradleCorrectnessRunner(
            EffectTransport(ProcessOutput(0), oversized), clock=Clock()
        ).run(self.request(result_limit_bytes=20))
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("CORRECTNESS_RESULT_COLLECTION_INVALID", attempt.reason_codes)

    def test_android_serial_must_be_explicitly_bound(self):
        with self.assertRaisesRegex(ValueError, "bind ANDROID_SERIAL"):
            self.request(environment={"JAVA_HOME": "/jdk"})

    def test_windows_wrapper_path_is_preserved_without_shell(self):
        (self.root / "gradlew.bat").write_text("@echo off\r\n")
        transport = EffectTransport(ProcessOutput(0), self.write_pass)
        GradleCorrectnessRunner(transport, clock=Clock()).run(
            self.request(wrapper_relative_path="gradlew.bat")
        )
        self.assertEqual(transport.calls[0].argv[0], str((self.root / "gradlew.bat").resolve()))

    def test_result_root_cannot_delete_apk_or_source(self):
        for path in ("app/build", "app/src/main/build/results"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "overlaps a protected input"):
                    self.request(result_root_relative_path=path)

    def test_execution_adapter_binds_report_to_correctness_state(self):
        request = self.request()
        adapter = CorrectnessExecutionAdapter(
            SimulatedAdapter(),
            GradleCorrectnessRunner(
                EffectTransport(ProcessOutput(0), self.write_pass), clock=Clock()
            ),
            {ExecutionState.VERIFYING_BASELINE_CORRECTNESS: request},
        )
        result = adapter.execute(
            ExecutionState.VERIFYING_BASELINE_CORRECTNESS,
            ExecutionSnapshot(request.run_id),
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(result.output_digests), 2)


if __name__ == "__main__":
    unittest.main()
