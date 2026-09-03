from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from causalperf_agent.android import (
    ProcessOutput,
    StartupStabilizationRequest,
    StartupStabilizationRunner,
    digest_tree,
)
from causalperf_reference.artifacts import digest, verify_content_digest


SERIAL = "LAB-DEVICE-001"


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        value = f"2026-08-30T03:00:{self.value:02d}Z"
        self.value += 1
        return value


class SequenceTransport:
    def __init__(self, outputs, effect=None):
        self.outputs = list(outputs)
        self.effect = effect
        self.calls = []

    def run(self, spec):
        self.calls.append(spec)
        if self.effect:
            self.effect(spec, len(self.calls))
        if not self.outputs:
            raise AssertionError(f"unexpected process: {spec.argv}")
        return self.outputs.pop(0)


def success_outputs():
    values = [ProcessOutput(0, b"package:/data/app/base.apk\n")]
    for _ in range(3):
        values.extend([
            ProcessOutput(0),
            ProcessOutput(0, b"Status: ok\nTotalTime: 123\n"),
            ProcessOutput(0),
        ])
    values.append(ProcessOutput(0))
    return values


class StartupStabilizationRunnerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "app/src/main"
        self.source.mkdir(parents=True)
        (self.source / "Main.kt").write_text("class Main\n")
        self.apk = self.root / "app/build/outputs/apk/benchmark/app.apk"
        self.apk.parent.mkdir(parents=True)
        self.apk.write_bytes(b"sealed-apk")

    def request(self, **overrides):
        value = {
            "run_id": "RUN-CPU-001-CAL-001", "arm": "A1",
            "task_root": self.root, "adb_executable": "/tools/adb",
            "device_serial": SERIAL, "device_serial_hash": digest(SERIAL),
            "package_name": "dev.causalperf.startup.cpu",
            "launch_component": "dev.causalperf.startup.cpu/.MainActivity",
            "environment": {"PATH": "/tools"},
            "source_relative_path": "app/src/main",
            "source_sha256": digest_tree(self.source),
            "apk_relative_path": "app/build/outputs/apk/benchmark/app.apk",
            "apk_sha256": hashlib.sha256(b"sealed-apk").hexdigest(),
            "environment_snapshot_id": "ENV-CPU-001-A1",
            "environment_snapshot_sha256": "a" * 64,
            "sequence_plan_sha256": "b" * 64,
        }
        value.update(overrides)
        return StartupStabilizationRequest(**value)

    def test_exactly_three_unmeasured_cold_launches_are_sealed(self):
        transport = SequenceTransport(success_outputs())
        attempt = StartupStabilizationRunner(transport, clock=Clock()).run(self.request())
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(attempt.result["iterations_completed"], 3)
        self.assertEqual(len([call for call in transport.calls if "start" in call.argv]), 3)
        self.assertEqual(
            transport.calls[2].argv,
            ("/tools/adb", "-s", SERIAL, "shell", "am", "start", "-W", "-n",
             "dev.causalperf.startup.cpu/.MainActivity"),
        )
        self.assertNotIn(SERIAL, json.dumps(attempt.result))
        verify_content_digest(attempt.result)

    def test_launch_failure_is_not_retried_and_final_stop_still_runs(self):
        outputs = [
            ProcessOutput(0, b"package:/data/app/base.apk"),
            ProcessOutput(0),
            ProcessOutput(0, b"Status: timeout\nTotalTime: 0\n"),
            ProcessOutput(0),
        ]
        transport = SequenceTransport(outputs)
        attempt = StartupStabilizationRunner(transport, clock=Clock()).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertEqual(attempt.reason_codes, ("STABILIZATION_LAUNCH_FAILED:0",))
        self.assertEqual(transport.calls[-1].argv[-4:], (
            "shell", "am", "force-stop", "dev.causalperf.startup.cpu"
        ))
        self.assertEqual(len([call for call in transport.calls if "start" in call.argv]), 1)

    def test_package_must_be_observably_installed(self):
        transport = SequenceTransport([ProcessOutput(0, b""), ProcessOutput(0)])
        attempt = StartupStabilizationRunner(transport, clock=Clock()).run(self.request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("STABILIZATION_PACKAGE_NOT_INSTALLED", attempt.reason_codes)

    def test_input_drift_stops_before_device_transport(self):
        request = self.request()
        self.apk.write_bytes(b"changed")
        transport = SequenceTransport([])
        with self.assertRaisesRegex(ValueError, "APK identity changed"):
            StartupStabilizationRunner(transport, clock=Clock()).run(request)
        self.assertEqual(transport.calls, [])

    def test_source_mutation_during_launch_is_fail(self):
        def mutate(_spec, count):
            if count == 3:
                (self.source / "Main.kt").write_text("changed\n")

        attempt = StartupStabilizationRunner(
            SequenceTransport(success_outputs(), mutate), clock=Clock()
        ).run(self.request())
        self.assertEqual(attempt.status, "FAIL")
        self.assertIn("SOURCE_CHANGED_DURING_STABILIZATION", attempt.reason_codes)

    def test_exact_iteration_count_and_component_scope_are_frozen(self):
        with self.assertRaisesRegex(ValueError, "exactly three"):
            self.request(iterations=2)
        with self.assertRaisesRegex(ValueError, "another package"):
            self.request(launch_component="com.attacker/.MainActivity")

    def test_windows_adb_path_remains_one_argv_item(self):
        adb = r"C:\Android\Sdk\platform-tools\adb.exe"
        transport = SequenceTransport(success_outputs())
        StartupStabilizationRunner(transport, clock=Clock()).run(
            self.request(adb_executable=adb, environment={"SystemRoot": r"C:\Windows"})
        )
        self.assertEqual(transport.calls[0].argv[0], adb)


if __name__ == "__main__":
    unittest.main()
