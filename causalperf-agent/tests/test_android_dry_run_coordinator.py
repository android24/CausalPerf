from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from causalperf_agent.android import (
    AdbCleanupRequest,
    AdbInstallRequest,
    AndroidDryRunCoordinator,
    AndroidDryRunPlan,
    GradleBuildRequest,
)
from causalperf_reference.artifacts import digest, verify_content_digest


SERIAL = "LAB-DEVICE-001"


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        result = f"2026-08-30T00:00:{self.value:02d}Z"
        self.value += 1
        return result


def operation(status="PASS", reasons=(), value="a"):
    return SimpleNamespace(
        status=status,
        reason_codes=tuple(reasons),
        result={"content_sha256": value * 64},
    )


class FakeBuilder:
    def __init__(self, attempt, events):
        self.attempt = attempt
        self.events = events

    def build(self, request):
        self.events.append("build")
        return self.attempt


class FakeCorrectness:
    def __init__(self, attempt, events):
        self.attempt = attempt
        self.events = events

    def run(self, request):
        self.events.append("correctness")
        return self.attempt


class AndroidDryRunCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.apk_sha = hashlib.sha256(b"apk").hexdigest()
        self.build_request = GradleBuildRequest(
            run_id="RUN-CPU-001-DRY-001", role="BASELINE", task_root=self.root,
            wrapper_relative_path="gradlew", args=("clean", ":app:assembleBenchmark"),
            environment={"JAVA_HOME": "/jdk"}, timeout_seconds=1200,
            output_limit_bytes=1000, source_relative_path="app/src/main",
            apk_relative_path="app/build/app.apk", source_artifact_id="AR-SOURCE-BASELINE",
            toolchain={"gradle": "9.5.0"},
        )
        self.cleanup_request = AdbCleanupRequest(
            run_id=self.build_request.run_id, task_root=self.root, adb_executable="/tools/adb",
            device_serial=SERIAL, device_serial_hash=digest(SERIAL),
            package_names=("dev.causalperf.startup.cpu",), environment={"PATH": "/tools"},
        )

    def build_attempt(self, status="PASS", reasons=()):
        apk = {"id": "AR-APK-BASELINE", "sha256": self.apk_sha} if status == "PASS" else None
        return SimpleNamespace(
            status=status, reason_codes=tuple(reasons), request=self.build_request,
            source_sha256="b" * 64, apk_artifact=apk,
            build_result={"content_sha256": "c" * 64},
        )

    def install_request(self, build):
        return AdbInstallRequest(
            run_id=build.request.run_id, task_root=self.root, adb_executable="/tools/adb",
            device_serial=SERIAL, device_serial_hash=digest(SERIAL),
            package_name="dev.causalperf.startup.cpu", apk_relative_path="app/build/app.apk",
            apk_artifact_id=build.apk_artifact["id"], apk_sha256=build.apk_artifact["sha256"],
            environment={"PATH": "/tools"},
        )

    def correctness_request(self, build):
        return SimpleNamespace(
            run_id=build.request.run_id, task_root=self.root,
            source_sha256=build.source_sha256, apk_sha256=build.apk_artifact["sha256"],
            device_serial_hash=digest(SERIAL),
        )

    def plan(self):
        return AndroidDryRunPlan(
            self.build_request.run_id, self.build_request,
            self.install_request, self.correctness_request, self.cleanup_request,
        )

    def coordinator(self, build, install, correctness, cleanups, events):
        def guarded_install(request):
            events.append("install")
            return install

        def cleanup(request):
            events.append("cleanup")
            return cleanups.pop(0)

        return AndroidDryRunCoordinator(
            builder=FakeBuilder(build, events), guarded_install=guarded_install,
            cleanup=cleanup, correctness_runner=FakeCorrectness(correctness, events),
            clock=Clock(),
        )

    def test_pass_sequence_is_build_preclean_install_correctness_postclean(self):
        events = []
        correctness = SimpleNamespace(
            status="PASS", reason_codes=(), report={"content_sha256": "d" * 64}
        )
        execution = self.coordinator(
            self.build_attempt(), operation(value="e"), correctness,
            [operation(value="f"), operation(value="0")], events,
        ).run(self.plan())
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(events, ["build", "cleanup", "install", "correctness", "cleanup"])
        verify_content_digest(execution.summary)
        self.assertEqual(execution.summary["scope"], "PUBLIC_TASK_ONLY")

    def test_install_failure_skips_correctness_but_still_cleans(self):
        events = []
        execution = self.coordinator(
            self.build_attempt(), operation("FAIL", ("ADB_INSTALL_FAILED",), "e"),
            SimpleNamespace(status="PASS", reason_codes=(), report={"content_sha256": "d" * 64}),
            [operation(value="f"), operation(value="0")], events,
        ).run(self.plan())
        self.assertEqual(execution.status, "FAIL")
        self.assertNotIn("correctness", events)
        self.assertEqual(events[-1], "cleanup")

    def test_post_cleanup_failure_vetoes_otherwise_passing_run(self):
        events = []
        correctness = SimpleNamespace(
            status="PASS", reason_codes=(), report={"content_sha256": "d" * 64}
        )
        execution = self.coordinator(
            self.build_attempt(), operation(value="e"), correctness,
            [operation(value="f"), operation("FAIL", ("ADB_UNINSTALL_FAILED",), "0")],
            events,
        ).run(self.plan())
        self.assertEqual(execution.status, "FAIL")
        self.assertIn("POST_CLEANUP:ADB_UNINSTALL_FAILED", execution.reason_codes)

    def test_install_transport_exception_still_runs_post_cleanup(self):
        events = []
        cleanups = [operation(value="f"), operation(value="0")]

        def install_raises(request):
            events.append("install")
            raise RuntimeError("transport crashed after a partial install")

        def cleanup(request):
            events.append("cleanup")
            return cleanups.pop(0)

        coordinator = AndroidDryRunCoordinator(
            builder=FakeBuilder(self.build_attempt(), events),
            guarded_install=install_raises,
            cleanup=cleanup,
            correctness_runner=FakeCorrectness(
                SimpleNamespace(
                    status="PASS", reason_codes=(),
                    report={"content_sha256": "d" * 64},
                ),
                events,
            ),
            clock=Clock(),
        )
        with self.assertRaisesRegex(RuntimeError, "partial install"):
            coordinator.run(self.plan())
        self.assertEqual(events, ["build", "cleanup", "install", "cleanup"])

    def test_build_failure_stops_before_any_device_action(self):
        events = []
        execution = self.coordinator(
            self.build_attempt("INCONCLUSIVE", ("GRADLE_TIMEOUT",)),
            operation(), SimpleNamespace(status="PASS", reason_codes=(), report={"content_sha256": "d" * 64}),
            [], events,
        ).run(self.plan())
        self.assertEqual(execution.status, "INCONCLUSIVE")
        self.assertEqual(events, ["build"])

    def test_mismatched_install_apk_is_rejected(self):
        events = []
        def mismatched(build):
            request = self.install_request(build)
            return AdbInstallRequest(**{**request.__dict__, "apk_sha256": "9" * 64})
        plan = AndroidDryRunPlan(
            self.build_request.run_id, self.build_request, mismatched,
            self.correctness_request, self.cleanup_request,
        )
        coordinator = self.coordinator(
            self.build_attempt(), operation(),
            SimpleNamespace(status="PASS", reason_codes=(), report={"content_sha256": "d" * 64}),
            [operation()], events,
        )
        with self.assertRaisesRegex(ValueError, "not bound to built APK"):
            coordinator.run(plan)


if __name__ == "__main__":
    unittest.main()
