from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from causalperf_reference.artifacts import digest

from .build import _contained, digest_tree, sha256_file
from .device import _validate_target
from .process import ProcessOutput, ProcessSpec, ProcessTransport


_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_CLASS = re.compile(r"^[A-Za-z0-9_.$]+$")


@dataclass(frozen=True)
class StartupStabilizationRequest:
    run_id: str
    arm: str
    task_root: Path
    adb_executable: str
    device_serial: str = field(repr=False)
    device_serial_hash: str
    package_name: str
    launch_component: str
    environment: Mapping[str, str] = field(repr=False)
    source_relative_path: str
    source_sha256: str
    apk_relative_path: str
    apk_sha256: str
    environment_snapshot_id: str
    environment_snapshot_sha256: str
    sequence_plan_sha256: str
    iterations: int = 3
    timeout_seconds: int = 60
    output_limit_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if self.arm not in {"A1", "B", "A2"}:
            raise ValueError(f"unsupported stabilization arm: {self.arm}")
        _validate_target(self.device_serial, self.device_serial_hash)
        if not _PACKAGE.fullmatch(self.package_name):
            raise ValueError("invalid Android package name")
        prefix = f"{self.package_name}/"
        if not self.launch_component.startswith(prefix):
            raise ValueError("launch component belongs to another package")
        component_class = self.launch_component[len(prefix):]
        if not component_class or not _CLASS.fullmatch(component_class):
            raise ValueError("invalid Android launch component")
        if not self.adb_executable:
            raise ValueError("ADB executable cannot be empty")
        if self.iterations != 3:
            raise ValueError("Phase 1B.4 requires exactly three stabilization launches")
        if self.timeout_seconds < 1 or self.output_limit_bytes < 1:
            raise ValueError("stabilization process limits must be positive")
        for label, value in (
            ("source", self.source_sha256),
            ("APK", self.apk_sha256),
            ("environment", self.environment_snapshot_sha256),
            ("sequence", self.sequence_plan_sha256),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"invalid {label} SHA-256")
        if not re.fullmatch(r"ENV-[A-Z0-9-]+", self.environment_snapshot_id):
            raise ValueError("invalid environment snapshot ID")
        environment = dict(self.environment)
        if any(not isinstance(key, str) or not key or not isinstance(value, str)
               for key, value in environment.items()):
            raise ValueError("stabilization environment must contain strings")
        object.__setattr__(self, "task_root", self.task_root.resolve())
        object.__setattr__(self, "environment", MappingProxyType(environment))

    @property
    def source_path(self) -> Path:
        return _contained(self.task_root, self.source_relative_path, "source path")

    @property
    def apk_path(self) -> Path:
        return _contained(self.task_root, self.apk_relative_path, "APK path")


@dataclass(frozen=True)
class StartupStabilizationAttempt:
    request: StartupStabilizationRequest
    status: str
    reason_codes: tuple[str, ...]
    result: dict

    @property
    def output_digests(self) -> tuple[str, ...]:
        return (self.result["content_sha256"],)


class StartupStabilizationRunner:
    """Execute exactly three unmeasured cold launches without automatic retry."""

    def __init__(self, transport: ProcessTransport, *, clock: Callable[[], str]):
        self.transport = transport
        self.clock = clock

    def _run(self, request: StartupStabilizationRequest, *arguments: str) -> ProcessOutput:
        return self.transport.run(
            ProcessSpec(
                argv=(request.adb_executable, "-s", request.device_serial, *arguments),
                working_directory=request.task_root,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=request.output_limit_bytes,
            )
        )

    def run(self, request: StartupStabilizationRequest) -> StartupStabilizationAttempt:
        source = request.source_path
        apk = request.apk_path
        if not source.is_dir() or source.is_symlink():
            raise ValueError("stabilization source is missing or unsafe")
        if not apk.is_file() or apk.is_symlink():
            raise ValueError("stabilization APK is missing or unsafe")
        if digest_tree(source) != request.source_sha256:
            raise ValueError("source identity changed before stabilization")
        if sha256_file(apk) != request.apk_sha256:
            raise ValueError("APK identity changed before stabilization")

        started_at = self.clock()
        reasons: list[str] = []
        process_facts: list[dict] = []
        package_query = self._run(request, "shell", "pm", "path", request.package_name)
        process_facts.append(_facts("PACKAGE_QUERY", None, package_query))
        if not _complete(package_query) or not any(
            line.strip().startswith("package:") for line in package_query.stdout.decode(errors="replace").splitlines()
        ):
            reasons.append("STABILIZATION_PACKAGE_NOT_INSTALLED")

        if not reasons:
            for sequence in range(request.iterations):
                stop = self._run(request, "shell", "am", "force-stop", request.package_name)
                process_facts.append(_facts("FORCE_STOP", sequence, stop))
                if not _complete(stop):
                    reasons.append(f"STABILIZATION_FORCE_STOP_FAILED:{sequence}")
                    break

                launch = self._run(
                    request, "shell", "am", "start", "-W", "-n", request.launch_component
                )
                process_facts.append(_facts("LAUNCH", sequence, launch))
                if not _launch_completed(launch):
                    reasons.append(f"STABILIZATION_LAUNCH_FAILED:{sequence}")
                    break

                home = self._run(request, "shell", "input", "keyevent", "HOME")
                process_facts.append(_facts("HOME", sequence, home))
                if not _complete(home):
                    reasons.append(f"STABILIZATION_HOME_FAILED:{sequence}")
                    break

        final_stop = self._run(request, "shell", "am", "force-stop", request.package_name)
        process_facts.append(_facts("FINAL_FORCE_STOP", None, final_stop))
        if not _complete(final_stop):
            reasons.append("STABILIZATION_FINAL_FORCE_STOP_FAILED")

        source_after = digest_tree(source)
        apk_after = sha256_file(apk) if apk.is_file() and not apk.is_symlink() else None
        status = "PASS" if not reasons else "INCONCLUSIVE"
        if source_after != request.source_sha256:
            status = "FAIL"
            reasons.append("SOURCE_CHANGED_DURING_STABILIZATION")
        if apk_after != request.apk_sha256:
            status = "FAIL"
            reasons.append("APK_CHANGED_DURING_STABILIZATION")

        completed_at = self.clock()
        result = {
            "schema_version": 1,
            "run_id": request.run_id,
            "arm": request.arm,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons)),
            "iterations_required": request.iterations,
            "iterations_completed": sum(
                item["step"] == "LAUNCH" and item["returncode"] == 0
                for item in process_facts
            ),
            "device_serial_hash": request.device_serial_hash,
            "package_name": request.package_name,
            "launch_component": request.launch_component,
            "source_sha256": request.source_sha256,
            "apk_sha256": request.apk_sha256,
            "environment_snapshot_id": request.environment_snapshot_id,
            "environment_snapshot_sha256": request.environment_snapshot_sha256,
            "sequence_plan_sha256": request.sequence_plan_sha256,
            "process_facts": process_facts,
        }
        result["content_sha256"] = digest(result, omit=("content_sha256",))
        return StartupStabilizationAttempt(
            request, status, tuple(result["reason_codes"]), result
        )


def _complete(output: ProcessOutput) -> bool:
    return output.returncode == 0 and not output.timed_out and not output.output_truncated


def _launch_completed(output: ProcessOutput) -> bool:
    if not _complete(output):
        return False
    text = output.stdout.decode("utf-8", errors="replace")
    return bool(
        re.search(r"^\s*Status:\s*ok\s*$", text, re.MULTILINE | re.IGNORECASE)
        and re.search(r"^\s*TotalTime:\s*\d+\s*$", text, re.MULTILINE)
    )


def _facts(step: str, sequence: int | None, output: ProcessOutput) -> dict:
    value = {
        "step": step,
        "returncode": output.returncode,
        "timed_out": output.timed_out,
        "output_truncated": output.output_truncated,
        "stdout_sha256": hashlib.sha256(output.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(output.stderr).hexdigest(),
    }
    if sequence is not None:
        value["sequence"] = sequence
    return value
