from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import (
    AdbCleanupRequest,
    AdbDeviceAdapter,
    AdbInstallExecutionAdapter,
    AdbInstallRequest,
    ProcessOutput,
)
from causalperf_agent.execution import ExecutionSnapshot, ExecutionState, SimulatedAdapter
from causalperf_agent.policy import GuardedExecutionAdapter, PolicyEngine, RuntimePolicy
from causalperf_reference.artifacts import digest


ROOT = Path(__file__).parents[2]
TOOL_SCHEMA = json.loads(
    (ROOT / "causalperf-agent/schemas/tool-contract.schema.json").read_text()
)
SERIAL = "LAB-DEVICE-001"


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        result = f"2026-08-30T00:00:{self.value:02d}Z"
        self.value += 1
        return result


class SequenceTransport:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, spec):
        self.calls.append(spec)
        if not self.outputs:
            raise AssertionError(f"unexpected process: {spec.argv}")
        value = self.outputs.pop(0)
        return value(spec) if callable(value) else value


def sealed(value):
    value["content_sha256"] = digest(value, omit=("content_sha256",))
    return value


class AdbDeviceAdapterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.apk = self.root / "app/build/app.apk"
        self.apk.parent.mkdir(parents=True)
        self.apk.write_bytes(b"sealed-apk")

    def install_request(self, **overrides):
        value = {
            "run_id": "RUN-CPU-001-DRY-001",
            "task_root": self.root,
            "adb_executable": "/tools/adb",
            "device_serial": SERIAL,
            "device_serial_hash": digest(SERIAL),
            "package_name": "dev.causalperf.startup.cpu",
            "apk_relative_path": "app/build/app.apk",
            "apk_artifact_id": "AR-APK-CPU-001",
            "apk_sha256": __import__("hashlib").sha256(b"sealed-apk").hexdigest(),
            "environment": {"PATH": "/tools"},
        }
        value.update(overrides)
        return AdbInstallRequest(**value)

    def cleanup_request(self, packages=("dev.causalperf.startup.cpu",)):
        return AdbCleanupRequest(
            run_id="RUN-CPU-001-DRY-001", task_root=self.root,
            adb_executable="/tools/adb", device_serial=SERIAL,
            device_serial_hash=digest(SERIAL), package_names=packages,
            environment={"PATH": "/tools"},
        )

    def test_install_is_exactly_targeted_and_verified(self):
        transport = SequenceTransport([
            ProcessOutput(0, b"Success\n"),
            ProcessOutput(0, b"package:/data/app/base.apk\n"),
        ])
        attempt = AdbDeviceAdapter(transport, clock=Clock()).install(
            self.install_request()
        )
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(
            transport.calls[0].argv,
            ("/tools/adb", "-s", SERIAL, "install", "-r", "--no-streaming", str(self.apk.resolve())),
        )
        self.assertEqual(
            transport.calls[1].argv[-4:],
            ("shell", "pm", "path", "dev.causalperf.startup.cpu"),
        )
        self.assertNotIn(SERIAL, json.dumps(attempt.result))

    def test_install_tool_request_uses_only_hashed_device_identity(self):
        tool = self.install_request().tool_request("2026-08-30T00:00:00Z")
        document = {"schema_version": 1, "tool_id": tool.tool_id, "request": tool.arguments}
        jsonschema.Draft202012Validator(TOOL_SCHEMA).validate(document)
        self.assertNotIn(SERIAL, json.dumps(document))

    def test_apk_digest_drift_stops_before_adb(self):
        request = self.install_request()
        self.apk.write_bytes(b"changed")
        transport = SequenceTransport([])
        with self.assertRaisesRegex(ValueError, "APK digest changed"):
            AdbDeviceAdapter(transport, clock=Clock()).install(request)
        self.assertEqual(transport.calls, [])

    def test_install_timeout_is_inconclusive(self):
        attempt = AdbDeviceAdapter(
            SequenceTransport([ProcessOutput(None, timed_out=True)]), clock=Clock()
        ).install(self.install_request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertEqual(attempt.reason_codes, ("ADB_INSTALL_TIMEOUT",))

    def test_windows_adb_path_is_preserved_as_one_exact_argv_item(self):
        executable = r"C:\Android\Sdk\platform-tools\adb.exe"
        transport = SequenceTransport([
            ProcessOutput(0, b"Success"),
            ProcessOutput(0, b"package:/data/app/base.apk"),
        ])
        AdbDeviceAdapter(transport, clock=Clock()).install(
            self.install_request(
                adb_executable=executable,
                environment={"SystemRoot": r"C:\Windows"},
            )
        )
        self.assertEqual(transport.calls[0].argv[0], executable)
        self.assertEqual(transport.calls[0].environment["SystemRoot"], r"C:\Windows")

    def test_zero_exit_without_installed_package_is_inconclusive(self):
        attempt = AdbDeviceAdapter(
            SequenceTransport([ProcessOutput(0, b"Success"), ProcessOutput(0, b"")]),
            clock=Clock(),
        ).install(self.install_request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertEqual(attempt.reason_codes, ("ADB_PACKAGE_NOT_VERIFIED",))

    def test_cleanup_force_stops_uninstalls_and_verifies_absence(self):
        transport = SequenceTransport([
            ProcessOutput(0, b"package:/data/app/base.apk\n"),
            ProcessOutput(0),
            ProcessOutput(0, b"Success\n"),
            ProcessOutput(0, b""),
        ])
        attempt = AdbDeviceAdapter(transport, clock=Clock()).cleanup(
            self.cleanup_request()
        )
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(transport.calls[1].argv[-4:], (
            "shell", "am", "force-stop", "dev.causalperf.startup.cpu"
        ))
        self.assertEqual(transport.calls[2].argv[-2:], (
            "uninstall", "dev.causalperf.startup.cpu"
        ))

    def test_cleanup_is_idempotent_when_package_is_absent(self):
        transport = SequenceTransport([ProcessOutput(0, b"")])
        attempt = AdbDeviceAdapter(transport, clock=Clock()).cleanup(
            self.cleanup_request()
        )
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(len(transport.calls), 1)

    def test_cleanup_transport_failure_is_not_mistaken_for_absence(self):
        attempt = AdbDeviceAdapter(
            SequenceTransport([ProcessOutput(1, stderr=b"device offline")]),
            clock=Clock(),
        ).cleanup(self.cleanup_request())
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("ADB_CLEANUP_QUERY_INCOMPLETE", attempt.reason_codes)

    def test_cleanup_failure_cannot_be_downgraded_by_later_inconclusive_package(self):
        transport = SequenceTransport([
            ProcessOutput(0, b"package:/data/app/one.apk"),
            ProcessOutput(0),
            ProcessOutput(1, stderr=b"uninstall failed"),
            ProcessOutput(1, stderr=b"device offline"),
        ])
        attempt = AdbDeviceAdapter(transport, clock=Clock()).cleanup(
            self.cleanup_request(("dev.causalperf.one", "dev.causalperf.two"))
        )
        self.assertEqual(attempt.status, "FAIL")
        self.assertIn("ADB_UNINSTALL_FAILED", attempt.reason_codes)
        self.assertIn("ADB_CLEANUP_QUERY_INCOMPLETE", attempt.reason_codes)

    def policy(self):
        return RuntimePolicy(sealed({
            "schema_version": 1, "id": "POL-CPU-001-DRY",
            "run_id": "RUN-CPU-001-DRY-001", "network": "denied",
            "readable_paths": ["app"], "writable_paths": ["app/src/main"],
            "protected_paths": ["app/src/androidTest"],
            "allowed_executables": {"build_variant": ["./gradlew"], "run_benchmark": ["./gradlew"]},
            "allowed_working_directories": ["."], "allowed_environment_keys": ["PATH"],
            "device_serial_hash": digest(SERIAL), "package_name": "dev.causalperf.startup.cpu",
            "allowed_partitions": ["DEVELOPMENT"], "task_approved_risks": ["R0", "R1"],
            "allow_external_publication": False,
            "budgets": {"tool_calls": 10, "wall_time_seconds": 5000, "experiments": 2,
                        "patch_files": 4, "patch_lines": 100, "output_bytes": 1_000_000},
        }))

    def test_install_executes_only_after_guarded_authorization(self):
        request = self.install_request()
        transport = SequenceTransport([
            ProcessOutput(0, b"Success"),
            ProcessOutput(0, b"package:/data/app/base.apk"),
        ])
        install = AdbInstallExecutionAdapter(
            SimulatedAdapter(), AdbDeviceAdapter(transport, clock=Clock()),
            lambda state, snapshot: request,
        )
        guarded = GuardedExecutionAdapter(
            install, PolicyEngine(self.policy(), clock=lambda: "2026-08-30T00:00:00Z"),
            lambda state, snapshot: install.tool_request(
                state, snapshot, "2026-08-30T00:00:00Z"
            ),
        )
        snapshot = ExecutionSnapshot(request.run_id)
        state = ExecutionState.VERIFYING_BASELINE_CORRECTNESS
        self.assertEqual(guarded.authorize(state, snapshot).status, "PASS")
        self.assertEqual(guarded.execute(state, snapshot).status, "PASS")
        self.assertEqual(len(transport.calls), 2)

    def test_policy_denial_prevents_install_transport(self):
        request = self.install_request(package_name="dev.causalperf.other")
        transport = SequenceTransport([])
        install = AdbInstallExecutionAdapter(
            SimulatedAdapter(), AdbDeviceAdapter(transport, clock=Clock()),
            lambda state, snapshot: request,
        )
        guarded = GuardedExecutionAdapter(
            install, PolicyEngine(self.policy()),
            lambda state, snapshot: install.tool_request(
                state, snapshot, "2026-08-30T00:00:00Z"
            ),
        )
        state = ExecutionState.VERIFYING_BASELINE_CORRECTNESS
        result = guarded.authorize(state, ExecutionSnapshot(request.run_id))
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
