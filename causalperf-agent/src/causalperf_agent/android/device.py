from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from causalperf_reference.artifacts import digest

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult
from causalperf_agent.policy.model import ToolRequest

from .build import sha256_file
from .process import ProcessOutput, ProcessSpec, ProcessTransport


INSTALL_STATES = {
    ExecutionState.VERIFYING_BASELINE_CORRECTNESS,
    ExecutionState.VERIFYING_TREATMENT_CORRECTNESS,
}
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_SERIAL = re.compile(r"^[A-Za-z0-9._:-]+$")


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]", "-", value.upper()).strip("-")
    if not normalized:
        raise ValueError("identifier cannot be normalized")
    return normalized


def _validate_target(device_serial: str, device_serial_hash: str) -> None:
    if not _SERIAL.fullmatch(device_serial):
        raise ValueError("invalid explicit ADB serial")
    if digest(device_serial) != device_serial_hash:
        raise ValueError("ADB serial does not match its sealed identity")


def _sealed_environment(environment: Mapping[str, str]) -> Mapping[str, str]:
    value = dict(environment)
    if any(not isinstance(key, str) or not key or not isinstance(item, str)
           for key, item in value.items()):
        raise ValueError("ADB environment must contain string keys and values")
    return MappingProxyType(value)


def _process_facts(output: ProcessOutput) -> dict:
    return {
        "returncode": output.returncode,
        "timed_out": output.timed_out,
        "output_truncated": output.output_truncated,
        "stdout_sha256": hashlib.sha256(output.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(output.stderr).hexdigest(),
    }


def _combine_status(current: str, candidate: str) -> str:
    priority = {"PASS": 0, "INCONCLUSIVE": 1, "FAIL": 2}
    return candidate if priority[candidate] > priority[current] else current


@dataclass(frozen=True)
class AdbInstallRequest:
    run_id: str
    task_root: Path
    adb_executable: str
    device_serial: str = field(repr=False)
    device_serial_hash: str
    package_name: str
    apk_relative_path: str
    apk_artifact_id: str
    apk_sha256: str
    environment: Mapping[str, str] = field(repr=False)
    timeout_seconds: int = 300
    output_limit_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        _validate_target(self.device_serial, self.device_serial_hash)
        if not _PACKAGE.fullmatch(self.package_name):
            raise ValueError("invalid Android package name")
        if not re.fullmatch(r"AR-[A-Z0-9-]+", self.apk_artifact_id):
            raise ValueError("invalid APK artifact ID")
        if not re.fullmatch(r"[a-f0-9]{64}", self.apk_sha256):
            raise ValueError("invalid APK digest")
        if not self.adb_executable:
            raise ValueError("ADB executable cannot be empty")
        if not 1 <= self.timeout_seconds <= 600 or self.output_limit_bytes < 1:
            raise ValueError("ADB timeout must be 1..600 seconds and output limit positive")
        object.__setattr__(self, "task_root", self.task_root.resolve())
        object.__setattr__(self, "environment", _sealed_environment(self.environment))

    @property
    def apk_path(self) -> Path:
        candidate = Path(self.apk_relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"unsafe APK path: {self.apk_relative_path}")
        current = self.task_root
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"APK path contains symlink: {self.apk_relative_path}")
        resolved = current.resolve()
        try:
            resolved.relative_to(self.task_root)
        except ValueError as error:
            raise ValueError("APK path escapes task root") from error
        return resolved

    def tool_request(self, requested_at: str) -> ToolRequest:
        return ToolRequest(
            "install_apk",
            {
                "device_serial_hash": self.device_serial_hash,
                "package_name": self.package_name,
                "apk_artifact_id": self.apk_artifact_id,
                "apk_sha256": self.apk_sha256,
                "timeout_seconds": self.timeout_seconds,
            },
            requested_at,
        )


@dataclass(frozen=True)
class AdbCleanupRequest:
    run_id: str
    task_root: Path
    adb_executable: str
    device_serial: str = field(repr=False)
    device_serial_hash: str
    package_names: tuple[str, ...]
    environment: Mapping[str, str] = field(repr=False)
    timeout_seconds: int = 120
    output_limit_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        _validate_target(self.device_serial, self.device_serial_hash)
        if not self.adb_executable:
            raise ValueError("ADB executable cannot be empty")
        packages = tuple(self.package_names)
        if not packages or len(set(packages)) != len(packages):
            raise ValueError("cleanup package names must be non-empty and unique")
        if any(not _PACKAGE.fullmatch(item) for item in packages):
            raise ValueError("invalid cleanup package name")
        if not 1 <= self.timeout_seconds <= 600 or self.output_limit_bytes < 1:
            raise ValueError("ADB timeout must be 1..600 seconds and output limit positive")
        object.__setattr__(self, "task_root", self.task_root.resolve())
        object.__setattr__(self, "package_names", packages)
        object.__setattr__(self, "environment", _sealed_environment(self.environment))


@dataclass(frozen=True)
class DeviceOperationAttempt:
    request: AdbInstallRequest | AdbCleanupRequest
    result: dict
    status: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError(f"invalid device operation status: {self.status}")

    @property
    def output_digests(self) -> tuple[str, ...]:
        return (self.result["content_sha256"],)


class AdbDeviceAdapter:
    def __init__(self, transport: ProcessTransport, *, clock: Callable[[], str]):
        self.transport = transport
        self.clock = clock

    def _run(self, request, *arguments: str) -> ProcessOutput:
        return self.transport.run(
            ProcessSpec(
                argv=(request.adb_executable, "-s", request.device_serial, *arguments),
                working_directory=request.task_root,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=request.output_limit_bytes,
            )
        )

    @staticmethod
    def _transport_status(output: ProcessOutput, prefix: str) -> tuple[str, list[str]]:
        if output.timed_out:
            return "INCONCLUSIVE", [f"{prefix}_TIMEOUT"]
        if output.output_truncated:
            return "INCONCLUSIVE", [f"{prefix}_OUTPUT_LIMIT_EXCEEDED"]
        if output.returncode != 0:
            return "FAIL", [f"{prefix}_FAILED"]
        return "PASS", []

    def install(self, request: AdbInstallRequest) -> DeviceOperationAttempt:
        apk = request.apk_path
        if not apk.is_file() or apk.is_symlink():
            raise ValueError(f"APK is not a regular file: {request.apk_relative_path}")
        if sha256_file(apk) != request.apk_sha256:
            raise ValueError("APK digest changed before installation")
        started_at = self.clock()
        install = self._run(
            request, "install", "-r", "--no-streaming", str(apk)
        )
        status, reasons = self._transport_status(install, "ADB_INSTALL")
        verify = None
        if status == "PASS":
            verify = self._run(request, "shell", "pm", "path", request.package_name)
            verify_status, verify_reasons = self._transport_status(
                verify, "ADB_INSTALL_VERIFY"
            )
            if verify_status != "PASS":
                status, reasons = verify_status, verify_reasons
            elif not any(
                line.strip().startswith("package:")
                for line in verify.stdout.decode("utf-8", errors="replace").splitlines()
            ):
                status, reasons = "INCONCLUSIVE", ["ADB_PACKAGE_NOT_VERIFIED"]
        completed_at = self.clock()
        result = {
            "schema_version": 1,
            "id": f"DOR-{_identifier(request.run_id)}-INSTALL",
            "run_id": request.run_id,
            "operation": "INSTALL",
            "started_at": started_at,
            "completed_at": completed_at,
            "device_serial_hash": request.device_serial_hash,
            "package_names": [request.package_name],
            "apk_artifact_id": request.apk_artifact_id,
            "apk_sha256": request.apk_sha256,
            "tool_request_sha256": request.tool_request(started_at).request_sha256,
            "processes": [_process_facts(install)] + (
                [_process_facts(verify)] if verify is not None else []
            ),
            "status": status,
            "reason_codes": reasons,
        }
        result["content_sha256"] = digest(result, omit=("content_sha256",))
        return DeviceOperationAttempt(request, result, status, tuple(reasons))

    def cleanup(self, request: AdbCleanupRequest) -> DeviceOperationAttempt:
        started_at = self.clock()
        processes: list[dict] = []
        reasons: list[str] = []
        status = "PASS"
        for package_name in request.package_names:
            query = self._run(request, "shell", "pm", "path", package_name)
            processes.append(_process_facts(query))
            if query.timed_out or query.output_truncated or query.returncode != 0:
                status = _combine_status(status, "INCONCLUSIVE")
                reasons.append("ADB_CLEANUP_QUERY_INCOMPLETE")
                continue
            installed = any(
                line.strip().startswith("package:")
                for line in query.stdout.decode("utf-8", errors="replace").splitlines()
            )
            if not installed:
                continue
            stop = self._run(request, "shell", "am", "force-stop", package_name)
            processes.append(_process_facts(stop))
            uninstall = self._run(request, "uninstall", package_name)
            processes.append(_process_facts(uninstall))
            uninstall_status, uninstall_reasons = self._transport_status(
                uninstall, "ADB_UNINSTALL"
            )
            if uninstall_status != "PASS":
                status = _combine_status(status, uninstall_status)
                reasons.extend(uninstall_reasons)
                continue
            verify = self._run(request, "shell", "pm", "path", package_name)
            processes.append(_process_facts(verify))
            if verify.timed_out or verify.output_truncated or verify.returncode != 0:
                status = _combine_status(status, "INCONCLUSIVE")
                reasons.append("ADB_CLEANUP_VERIFY_INCOMPLETE")
                continue
            still_installed = any(
                line.strip().startswith("package:")
                for line in verify.stdout.decode("utf-8", errors="replace").splitlines()
            )
            if still_installed:
                status = _combine_status(status, "FAIL")
                reasons.append("ADB_PACKAGE_STILL_INSTALLED")
        completed_at = self.clock()
        result = {
            "schema_version": 1,
            "id": f"DOR-{_identifier(request.run_id)}-CLEANUP",
            "run_id": request.run_id,
            "operation": "CLEANUP",
            "started_at": started_at,
            "completed_at": completed_at,
            "device_serial_hash": request.device_serial_hash,
            "package_names": list(request.package_names),
            "processes": processes,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons)),
        }
        result["content_sha256"] = digest(result, omit=("content_sha256",))
        return DeviceOperationAttempt(
            request, result, status, tuple(result["reason_codes"])
        )


InstallRequestFactory = Callable[
    [ExecutionState, ExecutionSnapshot], AdbInstallRequest | None
]


@dataclass
class AdbInstallExecutionAdapter:
    delegate: ExecutionAdapter
    device: AdbDeviceAdapter
    request_factory: InstallRequestFactory
    attempts: dict[ExecutionState, DeviceOperationAttempt] = field(
        default_factory=dict, init=False
    )
    _prepared: dict[ExecutionState, AdbInstallRequest] = field(
        default_factory=dict, init=False
    )

    def tool_request(
        self, state: ExecutionState, snapshot: ExecutionSnapshot, requested_at: str
    ) -> ToolRequest | None:
        if state not in INSTALL_STATES:
            return None
        request = self.request_factory(state, snapshot)
        if request is None:
            return None
        self._prepared[state] = request
        return request.tool_request(requested_at)

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        if state not in INSTALL_STATES:
            return self.delegate.execute(state, snapshot)
        request = self._prepared.pop(state, None)
        if request is None:
            return StepResult("FAIL", reason_codes=("INSTALL_AUTHORIZATION_MISSING",))
        if request.run_id != snapshot.run_id:
            return StepResult("FAIL", reason_codes=("INSTALL_RUN_ID_MISMATCH",))
        attempt = self.device.install(request)
        self.attempts[state] = attempt
        if attempt.status != "PASS":
            return StepResult(
                attempt.status,
                output_digests=attempt.output_digests,
                reason_codes=attempt.reason_codes,
            )
        delegated = self.delegate.execute(state, snapshot)
        return StepResult(
            delegated.status,
            output_digests=attempt.output_digests + delegated.output_digests,
            reason_codes=delegated.reason_codes,
            decision=delegated.decision,
        )

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)


@dataclass
class AdbCleanupExecutionAdapter:
    delegate: ExecutionAdapter
    device: AdbDeviceAdapter
    request: AdbCleanupRequest
    attempt: DeviceOperationAttempt | None = field(default=None, init=False)

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        if state != ExecutionState.CLEANING_UP:
            return self.delegate.execute(state, snapshot)
        if self.request.run_id != snapshot.run_id:
            return StepResult("FAIL", reason_codes=("CLEANUP_RUN_ID_MISMATCH",))
        self.attempt = self.device.cleanup(self.request)
        if self.attempt.status != "PASS":
            return StepResult(
                self.attempt.status,
                output_digests=self.attempt.output_digests,
                reason_codes=self.attempt.reason_codes,
            )
        delegated = self.delegate.execute(state, snapshot)
        return StepResult(
            delegated.status,
            output_digests=self.attempt.output_digests + delegated.output_digests,
            reason_codes=delegated.reason_codes,
            decision=delegated.decision,
        )

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        cleanup = self.device.cleanup(self.request)
        self.attempt = cleanup
        delegated = self.delegate.rollback(snapshot)
        if cleanup.status != "PASS":
            return StepResult(
                cleanup.status,
                output_digests=cleanup.output_digests + delegated.output_digests,
                reason_codes=cleanup.reason_codes + delegated.reason_codes,
            )
        return StepResult(
            delegated.status,
            output_digests=cleanup.output_digests + delegated.output_digests,
            reason_codes=delegated.reason_codes,
        )
