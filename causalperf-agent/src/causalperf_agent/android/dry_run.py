from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from causalperf_reference.artifacts import digest

from .build import GradleBuildAdapter, GradleBuildAttempt, GradleBuildRequest
from .correctness_runner import (
    GradleCorrectnessRequest,
    GradleCorrectnessRunAttempt,
    GradleCorrectnessRunner,
)
from .device import (
    AdbCleanupRequest,
    AdbInstallRequest,
    DeviceOperationAttempt,
)


InstallRequestFactory = Callable[[GradleBuildAttempt], AdbInstallRequest]
CorrectnessRequestFactory = Callable[[GradleBuildAttempt], GradleCorrectnessRequest]
GuardedInstallExecutor = Callable[[AdbInstallRequest], DeviceOperationAttempt]
CleanupExecutor = Callable[[AdbCleanupRequest], DeviceOperationAttempt]


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]", "-", value.upper()).strip("-")
    if not normalized:
        raise ValueError("identifier cannot be normalized")
    return normalized


@dataclass(frozen=True)
class AndroidDryRunPlan:
    run_id: str
    build_request: GradleBuildRequest
    install_request_factory: InstallRequestFactory
    correctness_request_factory: CorrectnessRequestFactory
    cleanup_request: AdbCleanupRequest

    def __post_init__(self) -> None:
        if self.build_request.run_id != self.run_id:
            raise ValueError("dry-run build uses another run ID")
        if self.cleanup_request.run_id != self.run_id:
            raise ValueError("dry-run cleanup uses another run ID")
        if self.build_request.role != "BASELINE":
            raise ValueError("public dry run requires a baseline build")
        if self.cleanup_request.task_root != self.build_request.task_root.resolve():
            raise ValueError("dry-run cleanup uses another task workspace")


@dataclass(frozen=True)
class AndroidDryRunExecution:
    plan: AndroidDryRunPlan
    summary: dict
    build: GradleBuildAttempt
    pre_cleanup: DeviceOperationAttempt | None
    install: DeviceOperationAttempt | None
    correctness: GradleCorrectnessRunAttempt | None
    post_cleanup: DeviceOperationAttempt | None

    @property
    def status(self) -> str:
        return self.summary["status"]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(self.summary["reason_codes"])


class AndroidDryRunCoordinator:
    """Run the public DEVELOPMENT build/install/correctness/cleanup lane.

    `guarded_install` must be the policy-authorized install boundary. Private
    correctness and restored-build evaluation remain Bench/evaluator concerns.
    """

    def __init__(
        self,
        *,
        builder: GradleBuildAdapter,
        guarded_install: GuardedInstallExecutor,
        cleanup: CleanupExecutor,
        correctness_runner: GradleCorrectnessRunner,
        clock: Callable[[], str],
    ):
        self.builder = builder
        self.guarded_install = guarded_install
        self.cleanup = cleanup
        self.correctness_runner = correctness_runner
        self.clock = clock

    @staticmethod
    def _validate_install(
        build: GradleBuildAttempt, request: AdbInstallRequest
    ) -> None:
        apk = build.apk_artifact
        if apk is None:
            raise ValueError("passing build lacks APK artifact")
        if request.run_id != build.request.run_id:
            raise ValueError("install request uses another run ID")
        if request.apk_artifact_id != apk["id"] or request.apk_sha256 != apk["sha256"]:
            raise ValueError("install request is not bound to built APK")
        if request.task_root != build.request.task_root.resolve():
            raise ValueError("install request uses another task workspace")

    @staticmethod
    def _validate_correctness(
        build: GradleBuildAttempt,
        install: AdbInstallRequest,
        request: GradleCorrectnessRequest,
    ) -> None:
        if request.run_id != build.request.run_id:
            raise ValueError("correctness request uses another run ID")
        if request.task_root != build.request.task_root.resolve():
            raise ValueError("correctness request uses another task workspace")
        if request.source_sha256 != build.source_sha256:
            raise ValueError("correctness request is not bound to built source")
        if request.apk_sha256 != install.apk_sha256:
            raise ValueError("correctness request is not bound to installed APK")
        if request.device_serial_hash != install.device_serial_hash:
            raise ValueError("correctness request uses another device")

    @staticmethod
    def _outcome(stages: list[tuple[str, object]]) -> tuple[str, list[str]]:
        status = "PASS"
        reasons: list[str] = []
        for label, attempt in stages:
            attempt_status = getattr(attempt, "status")
            if attempt_status == "FAIL":
                status = "FAIL"
            elif attempt_status == "INCONCLUSIVE" and status != "FAIL":
                status = "INCONCLUSIVE"
            reasons.extend(
                f"{label}:{reason}" for reason in getattr(attempt, "reason_codes", ())
            )
        return status, list(dict.fromkeys(reasons))

    def run(self, plan: AndroidDryRunPlan) -> AndroidDryRunExecution:
        started_at = self.clock()
        build = self.builder.build(plan.build_request)
        pre_cleanup = install = correctness = post_cleanup = None
        stages: list[tuple[str, object]] = [("BUILD", build)]

        if build.status == "PASS":
            pre_cleanup = self.cleanup(plan.cleanup_request)
            stages.append(("PRE_CLEANUP", pre_cleanup))
            if pre_cleanup.status == "PASS":
                install_request = plan.install_request_factory(build)
                self._validate_install(build, install_request)
                if install_request.package_name not in plan.cleanup_request.package_names:
                    raise ValueError("cleanup does not own the installed package")
                if (
                    install_request.device_serial_hash
                    != plan.cleanup_request.device_serial_hash
                ):
                    raise ValueError("cleanup uses another device")
                try:
                    install = self.guarded_install(install_request)
                    stages.append(("INSTALL", install))
                    if install.status == "PASS":
                        correctness_request = plan.correctness_request_factory(build)
                        self._validate_correctness(
                            build, install_request, correctness_request
                        )
                        correctness = self.correctness_runner.run(correctness_request)
                        stages.append(("CORRECTNESS", correctness))
                finally:
                    post_cleanup = self.cleanup(plan.cleanup_request)
                    stages.append(("POST_CLEANUP", post_cleanup))

        status, reasons = self._outcome(stages)
        completed_at = self.clock()
        stage_digests = {
            "build": build.build_result["content_sha256"],
        }
        if pre_cleanup is not None:
            stage_digests["pre_cleanup"] = pre_cleanup.result["content_sha256"]
        if install is not None:
            stage_digests["install"] = install.result["content_sha256"]
        if correctness is not None:
            stage_digests["correctness"] = correctness.report["content_sha256"]
        if post_cleanup is not None:
            stage_digests["post_cleanup"] = post_cleanup.result["content_sha256"]
        summary = {
            "schema_version": 1,
            "id": f"APDR-{_identifier(plan.run_id)}",
            "run_id": plan.run_id,
            "partition": "DEVELOPMENT",
            "started_at": started_at,
            "completed_at": completed_at,
            "scope": "PUBLIC_TASK_ONLY",
            "stage_digests": stage_digests,
            "status": status,
            "reason_codes": reasons,
        }
        summary["content_sha256"] = digest(summary, omit=("content_sha256",))
        return AndroidDryRunExecution(
            plan, summary, build, pre_cleanup, install, correctness, post_cleanup
        )
