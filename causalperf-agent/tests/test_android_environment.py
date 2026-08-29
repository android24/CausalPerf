import json
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import (
    AndroidEnvironmentCollector,
    AndroidLabRequirements,
    CommandOutput,
    PreflightExecutionAdapter,
    PreflightResult,
    SubprocessCommandRunner,
)
from causalperf_agent.execution import ExperimentController, ExecutionState, SimulatedAdapter
from causalperf_reference.artifacts import digest, verify_content_digest


REPO_ROOT = Path(__file__).parents[2]
ENVIRONMENT_SCHEMA = json.loads(
    (REPO_ROOT / "shared" / "schemas" / "environment-snapshot.schema.json").read_text()
)
SERIAL = "LAB-DEVICE-001"
JAVA = "/tools/java"
ADB = "/tools/adb"
GRADLE = "/tools/gradle"


class FakeRunner:
    def __init__(self, responses):
        self.responses = {
            tuple(command): list(value) if isinstance(value, list) else [value]
            for command, value in responses.items()
        }
        self.calls = []

    def run(self, argv, timeout_seconds):
        command = tuple(argv)
        self.calls.append((command, timeout_seconds))
        if command not in self.responses or not self.responses[command]:
            raise AssertionError(f"unexpected command: {command}")
        return self.responses[command].pop(0)


def success_responses(overrides=None):
    shell = (ADB, "-s", SERIAL, "shell")
    battery = """
      AC powered: false
      USB powered: true
      Wireless powered: false
      Dock powered: false
      level: 85
    """
    values = {
        (JAVA, "-version"): CommandOutput(0, stderr='openjdk version "17.0.12" 2024-07-16'),
        (ADB, "version"): CommandOutput(0, "Android Debug Bridge version 1.0.41\nVersion 35.0.2"),
        (GRADLE, "--version"): CommandOutput(0, "Gradle 9.5.0\n"),
        (ADB, "devices"): CommandOutput(0, f"List of devices attached\n{SERIAL}\tdevice\n"),
        shell + ("getprop", "ro.build.version.sdk"): CommandOutput(0, "35\n"),
        shell + ("getprop", "ro.product.model"): CommandOutput(0, "Pixel 8\n"),
        shell + ("getprop", "ro.product.cpu.abi"): CommandOutput(0, "arm64-v8a\n"),
        shell + ("getprop", "ro.build.fingerprint"): CommandOutput(0, "google/test/build:15/id:user/release-keys\n"),
        shell + ("getprop", "ro.kernel.qemu"): CommandOutput(0, "0\n"),
        shell + ("dumpsys", "battery"): CommandOutput(0, battery),
        shell + ("dumpsys", "thermalservice"): CommandOutput(0, "Current Thermal Status: 0\n"),
        shell + ("cat", "/sys/devices/system/cpu/online"): CommandOutput(0, "0-7\n"),
        shell + ("cat", "/proc/meminfo"): CommandOutput(0, "MemAvailable:       6291456 kB\n"),
        shell + ("cat", "/proc/stat"): [
            CommandOutput(0, "cpu  100 0 50 800 50 0 0 0 0 0\n"),
            CommandOutput(0, "cpu  110 0 55 875 50 0 0 0 0 0\n"),
        ],
    }
    values.update(overrides or {})
    return values


class AndroidEnvironmentCollectorTest(unittest.TestCase):
    def collector(self, responses, *, tools=True, environment=None):
        paths = {"java": JAVA, "adb": ADB, "gradle": GRADLE} if tools else {}
        return AndroidEnvironmentCollector(
            runner=FakeRunner(responses),
            which=paths.get,
            path_exists=lambda path: True,
            environment={"ANDROID_SDK_ROOT": "/sdk"} if environment is None else environment,
            clock=lambda: "2026-08-24T00:00:00Z",
            sleep=lambda seconds: None,
        )

    def test_pass_emits_schema_valid_environment_snapshot(self):
        collector = self.collector(success_responses())
        result = collector.collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-CAL-001",
            requirements=AndroidLabRequirements(expected_online_cpu_count=8),
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reason_codes, ())
        snapshot = result.environment_snapshot
        jsonschema.Draft202012Validator(
            ENVIRONMENT_SCHEMA, format_checker=jsonschema.FormatChecker()
        ).validate(snapshot)
        verify_content_digest(snapshot)
        self.assertEqual(snapshot["device"]["serial_hash"], digest(SERIAL))
        self.assertNotIn(SERIAL, json.dumps(snapshot))
        self.assertEqual(snapshot["runtime"]["online_cpu_count"], 8)
        self.assertAlmostEqual(snapshot["runtime"]["background_load_percent"], 16.667)

    def test_missing_host_tools_fails_before_any_device_command(self):
        collector = self.collector({}, tools=False, environment={})
        result = collector.collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(
            result.reason_codes,
            (
                "HOST_TOOL_MISSING:JAVA",
                "HOST_TOOL_MISSING:ADB",
                "HOST_TOOL_MISSING:GRADLE",
                "ANDROID_SDK_ROOT_MISSING",
            ),
        )
        self.assertEqual(collector.runner.calls, [])
        self.assertIsNone(result.environment_snapshot)

    def test_tools_and_sdk_can_be_late_bound_without_environment_changes(self):
        collector = AndroidEnvironmentCollector(
            runner=FakeRunner(success_responses()),
            which=lambda tool: None,
            path_exists=lambda path: True,
            environment={},
            tool_paths={"java": JAVA, "adb": ADB, "gradle": GRADLE},
            sdk_root="/late-bound-sdk",
            clock=lambda: "2026-08-25T00:00:00Z",
            sleep=lambda seconds: None,
        )

        result = collector.collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-LATE-BOUND",
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.environment_snapshot["toolchain"]["java"], "17.0.12")

    def test_missing_explicit_tool_does_not_fall_back_to_ambient_path(self):
        collector = AndroidEnvironmentCollector(
            runner=FakeRunner({}),
            which=lambda tool: f"/ambient/{tool}",
            path_exists=lambda path: not str(path).startswith("/pinned/"),
            environment={"ANDROID_SDK_ROOT": "/sdk"},
            tool_paths={"java": "/pinned/java", "adb": "/pinned/adb", "gradle": "/pinned/gradle"},
        )

        result = collector.collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-MISSING-PIN",
        )

        self.assertEqual(
            result.reason_codes[:3],
            (
                "HOST_TOOL_MISSING:JAVA",
                "HOST_TOOL_MISSING:ADB",
                "HOST_TOOL_MISSING:GRADLE",
            ),
        )
        self.assertEqual(collector.runner.calls, [])

    def test_missing_sdk_components_do_not_invoke_the_gradle_wrapper(self):
        runner = FakeRunner({})
        collector = AndroidEnvironmentCollector(
            runner=runner,
            which=lambda tool: {"java": JAVA, "adb": ADB, "gradle": GRADLE}[tool],
            path_exists=lambda path: not (
                "platforms/android-36/android.jar" in str(path)
                or "build-tools/36.0.0" in str(path)
            ),
            environment={"ANDROID_SDK_ROOT": "/partial-sdk"},
        )

        result = collector.collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        self.assertEqual(
            result.reason_codes,
            ("ANDROID_PLATFORM_MISSING", "ANDROID_BUILD_TOOLS_MISSING"),
        )
        self.assertEqual(runner.calls, [])

    def test_java_and_gradle_versions_are_fail_closed(self):
        responses = success_responses(
            {
                (JAVA, "-version"): CommandOutput(0, stderr='java version "1.8.0_421"'),
                (GRADLE, "--version"): CommandOutput(0, "Gradle 8.10.2\n"),
            }
        )
        result = self.collector(responses).collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(
            result.reason_codes,
            ("JAVA_VERSION_UNSUPPORTED", "GRADLE_VERSION_MISMATCH"),
        )

    def test_requested_device_must_exist_and_be_online(self):
        responses = success_responses(
            {(ADB, "devices"): CommandOutput(0, "List of devices attached\nOTHER\tdevice\n")}
        )
        result = self.collector(responses).collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(result.reason_codes, ("REQUESTED_DEVICE_NOT_FOUND",))

    def test_environment_violations_are_recorded_in_snapshot(self):
        shell = (ADB, "-s", SERIAL, "shell")
        battery = """
          AC powered: false
          USB powered: false
          Wireless powered: false
          Dock powered: false
          level: 20
        """
        responses = success_responses(
            {
                shell + ("getprop", "ro.build.version.sdk"): CommandOutput(0, "33\n"),
                shell + ("getprop", "ro.product.cpu.abi"): CommandOutput(0, "x86_64\n"),
                shell + ("getprop", "ro.kernel.qemu"): CommandOutput(0, "1\n"),
                shell + ("dumpsys", "battery"): CommandOutput(0, battery),
                shell + ("dumpsys", "thermalservice"): CommandOutput(0, "mStatus=3\n"),
                shell + ("cat", "/proc/meminfo"): CommandOutput(0, "MemAvailable:       1048576 kB\n"),
                shell + ("cat", "/proc/stat"): [
                    CommandOutput(0, "cpu  100 0 50 800 50 0 0 0 0 0\n"),
                    CommandOutput(0, "cpu  130 0 70 840 50 0 0 0 0 0\n"),
                ],
            }
        )
        result = self.collector(responses).collect(
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
            requirements=AndroidLabRequirements(expected_online_cpu_count=4),
        )

        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(
            result.reason_codes,
            (
                "API_LEVEL_OUT_OF_RANGE",
                "ABI_NOT_ALLOWED",
                "PHYSICAL_DEVICE_REQUIRED",
                "BATTERY_BELOW_MINIMUM",
                "THERMAL_STATUS_NOT_ALLOWED",
                "ONLINE_CPU_COUNT_MISMATCH",
                "AVAILABLE_MEMORY_BELOW_MINIMUM",
                "BACKGROUND_LOAD_ABOVE_MAXIMUM",
            ),
        )
        jsonschema.validate(result.environment_snapshot, ENVIRONMENT_SCHEMA)
        verify_content_digest(result.environment_snapshot)

    def test_explicit_serial_is_required(self):
        result = self.collector(success_responses()).collect(
            device_serial="",
            environment_id="ENV-CPU-001-PREFLIGHT",
        )
        self.assertEqual(result.reason_codes, ("EXPLICIT_DEVICE_SERIAL_REQUIRED",))

    def test_subprocess_environment_supports_windows_without_forwarding_secrets(self):
        runner = SubprocessCommandRunner(
            {
                "PATH": r"C:\Windows\System32",
                "USERPROFILE": r"C:\Users\runner",
                "SystemRoot": r"C:\Windows",
                "ComSpec": r"C:\Windows\System32\cmd.exe",
                "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                "SHOULD_NOT_LEAK": "secret",
            }
        )

        self.assertEqual(runner.environment["ComSpec"], r"C:\Windows\System32\cmd.exe")
        self.assertNotIn("SHOULD_NOT_LEAK", runner.environment)


class StubCollector:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def collect(self, **arguments):
        self.calls.append(arguments)
        return self.result


class PreflightExecutionAdapterTest(unittest.TestCase):
    def test_controller_stops_before_build_when_preflight_is_inconclusive(self):
        delegate = SimulatedAdapter()
        collector = StubCollector(
            PreflightResult("INCONCLUSIVE", ("REQUESTED_DEVICE_NOT_FOUND",))
        )
        adapter = PreflightExecutionAdapter(
            delegate,
            collector,
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        result = ExperimentController("RUN-PREFLIGHT-FAIL", adapter).run()

        self.assertEqual(result.state, ExecutionState.INCONCLUSIVE)
        self.assertEqual(delegate.executed, [ExecutionState.VALIDATING])
        self.assertNotIn(ExecutionState.BUILDING_BASELINE, delegate.executed)
        self.assertEqual(result.reason_codes, ["REQUESTED_DEVICE_NOT_FOUND"])

    def test_passing_snapshot_digest_enters_ledger_before_build(self):
        snapshot_digest = "a" * 64
        delegate = SimulatedAdapter()
        collector = StubCollector(
            PreflightResult("PASS", (), {"content_sha256": snapshot_digest})
        )
        adapter = PreflightExecutionAdapter(
            delegate,
            collector,
            device_serial=SERIAL,
            environment_id="ENV-CPU-001-PREFLIGHT",
        )

        controller = ExperimentController("RUN-PREFLIGHT-PASS", adapter)
        result = controller.run()

        self.assertEqual(result.state, ExecutionState.COMPLETED)
        self.assertIn(snapshot_digest, result.artifact_digests)
        self.assertNotIn(ExecutionState.PREPARING_ENVIRONMENT, delegate.executed)
        completions = [
            event
            for event in controller.ledger.events
            if event["phase"] == ExecutionState.PREPARING_ENVIRONMENT.value
            and event["kind"] == "COMPLETION"
        ]
        self.assertEqual(completions[0]["outputs"], (snapshot_digest,))


if __name__ == "__main__":
    unittest.main()
