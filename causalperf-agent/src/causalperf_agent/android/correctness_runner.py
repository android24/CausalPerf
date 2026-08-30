from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from causalperf_reference.artifacts import digest

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult

from .build import digest_tree, sha256_file
from .correctness import (
    CorrectnessAttempt,
    CorrectnessEvidenceRequest,
    CorrectnessReportParser,
)
from .device import _validate_target
from .process import ProcessSpec, ProcessTransport


CORRECTNESS_STATES = {
    ExecutionState.VERIFYING_BASELINE_CORRECTNESS,
    ExecutionState.VERIFYING_TREATMENT_CORRECTNESS,
}


def _contained(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe {label}: {relative}")
    current = root.resolve()
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains symlink: {relative}")
    resolved = current.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escapes task root: {relative}") from error
    return resolved


@dataclass(frozen=True)
class GradleCorrectnessRequest:
    run_id: str
    phase: str
    task_root: Path
    wrapper_relative_path: str
    args: tuple[str, ...]
    environment: Mapping[str, str] = field(repr=False)
    timeout_seconds: int
    output_limit_bytes: int
    device_serial: str = field(repr=False)
    device_serial_hash: str
    source_relative_path: str
    source_manifest_id: str
    source_sha256: str
    apk_relative_path: str
    apk_sha256: str
    suite_id: str
    suite_sha256: str
    result_root_relative_path: str
    result_limit_bytes: int = 20_000_000

    def __post_init__(self) -> None:
        if self.phase not in {"BASELINE", "TREATMENT"}:
            raise ValueError(f"unsupported correctness phase: {self.phase}")
        _validate_target(self.device_serial, self.device_serial_hash)
        if not self.args or any(not isinstance(item, str) or not item for item in self.args):
            raise ValueError("correctness command args must be non-empty strings")
        if not re.fullmatch(r"SM-[A-Z0-9-]+", self.source_manifest_id):
            raise ValueError("invalid source manifest ID")
        for label, value in (
            ("source_sha256", self.source_sha256),
            ("apk_sha256", self.apk_sha256),
            ("suite_sha256", self.suite_sha256),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"invalid {label}")
        if not self.suite_id:
            raise ValueError("suite_id cannot be empty")
        if min(self.timeout_seconds, self.output_limit_bytes, self.result_limit_bytes) < 1:
            raise ValueError("correctness limits must be positive")
        environment = dict(self.environment)
        if environment.get("ANDROID_SERIAL") != self.device_serial:
            raise ValueError("correctness environment must bind ANDROID_SERIAL")
        if any(not isinstance(key, str) or not key or not isinstance(value, str)
               for key, value in environment.items()):
            raise ValueError("correctness environment must contain strings")
        object.__setattr__(self, "task_root", self.task_root.resolve())
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "environment", MappingProxyType(environment))

        result_candidate = Path(self.result_root_relative_path)
        if "build" not in result_candidate.parts:
            raise ValueError("JUnit result root must be inside a build output tree")
        result_root = self.result_root
        source = self.source_path
        apk = self.apk_path
        if (
            result_root == source
            or result_root in source.parents
            or source in result_root.parents
            or result_root == apk
            or result_root in apk.parents
        ):
            raise ValueError("JUnit result root overlaps a protected input")

    @property
    def wrapper_path(self) -> Path:
        return _contained(self.task_root, self.wrapper_relative_path, "Wrapper path")

    @property
    def source_path(self) -> Path:
        return _contained(self.task_root, self.source_relative_path, "source path")

    @property
    def apk_path(self) -> Path:
        return _contained(self.task_root, self.apk_relative_path, "APK path")

    @property
    def result_root(self) -> Path:
        return _contained(
            self.task_root, self.result_root_relative_path, "JUnit result root"
        )

    def command(self) -> dict:
        executable = self.wrapper_relative_path.replace("\\", "/")
        if not executable.startswith("./"):
            executable = f"./{executable}"
        return {
            "executable": executable,
            "args": list(self.args),
            "working_directory": ".",
            "environment": dict(self.environment),
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
        }


@dataclass(frozen=True)
class GradleCorrectnessRunAttempt:
    request: GradleCorrectnessRequest
    correctness: CorrectnessAttempt
    status: str
    reason_codes: tuple[str, ...]
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)

    @property
    def report(self) -> dict:
        return self.correctness.report

    @property
    def output_digests(self) -> tuple[str, ...]:
        return (
            self.report["content_sha256"],
            self.report["result_artifact_sha256"],
        )


class GradleCorrectnessRunner:
    def __init__(
        self,
        transport: ProcessTransport,
        *,
        clock: Callable[[], str],
        parser: CorrectnessReportParser | None = None,
    ):
        self.transport = transport
        self.clock = clock
        self.parser = parser or CorrectnessReportParser()

    @staticmethod
    def _clear_result_root(root: Path) -> None:
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"JUnit result root is not a real directory: {root}")
        shutil.rmtree(root)

    @staticmethod
    def _collect_documents(root: Path, limit: int) -> Mapping[str, bytes]:
        if not root.exists():
            return {}
        if root.is_symlink() or not root.is_dir():
            raise ValueError("JUnit result root is not a real directory")
        documents: dict[str, bytes] = {}
        total = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"JUnit result tree contains symlink: {path}")
            if not path.is_file() or path.suffix.casefold() != ".xml":
                continue
            payload = path.read_bytes()
            total += len(payload)
            if total > limit:
                raise ValueError("JUnit result byte limit exceeded")
            documents[path.relative_to(root).as_posix()] = payload
        return documents

    def run(self, request: GradleCorrectnessRequest) -> GradleCorrectnessRunAttempt:
        wrapper = request.wrapper_path
        source = request.source_path
        apk = request.apk_path
        result_root = request.result_root
        if not wrapper.is_file() or wrapper.is_symlink():
            raise ValueError("Gradle Wrapper is missing or unsafe")
        if not source.is_dir() or source.is_symlink():
            raise ValueError("correctness source root is missing or unsafe")
        if not apk.is_file() or apk.is_symlink():
            raise ValueError("correctness APK is missing or unsafe")
        source_before = digest_tree(source)
        apk_before = sha256_file(apk)
        if source_before != request.source_sha256:
            raise ValueError("correctness source does not match sealed source")
        if apk_before != request.apk_sha256:
            raise ValueError("correctness APK does not match sealed APK")

        self._clear_result_root(result_root)
        started_at = self.clock()
        output = self.transport.run(
            ProcessSpec(
                argv=(str(wrapper), *request.args),
                working_directory=request.task_root,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=request.output_limit_bytes,
            )
        )
        completed_at = self.clock()
        collection_reasons: list[str] = []
        try:
            documents = self._collect_documents(
                result_root, request.result_limit_bytes
            )
        except (OSError, ValueError):
            documents = {}
            collection_reasons.append("CORRECTNESS_RESULT_COLLECTION_INVALID")

        evidence = CorrectnessEvidenceRequest(
            run_id=request.run_id,
            phase=request.phase,
            suite_id=request.suite_id,
            suite_sha256=request.suite_sha256,
            source_manifest_id=request.source_manifest_id,
            source_sha256=request.source_sha256,
            apk_sha256=request.apk_sha256,
            command=request.command(),
            started_at=started_at,
            completed_at=completed_at,
            exit_code=output.returncode if output.returncode is not None else -1,
            result_documents=documents,
            process_timed_out=output.timed_out,
            process_output_truncated=output.output_truncated,
        )
        correctness = self.parser.evaluate(evidence)
        reasons = list(correctness.reason_codes) + collection_reasons
        status = correctness.status
        if digest_tree(source) != source_before:
            status = "FAIL"
            reasons.append("SOURCE_CHANGED_DURING_CORRECTNESS")
        if not apk.is_file() or sha256_file(apk) != apk_before:
            status = "FAIL"
            reasons.append("APK_CHANGED_DURING_CORRECTNESS")
        if collection_reasons and status == "PASS":
            status = "INCONCLUSIVE"
        return GradleCorrectnessRunAttempt(
            request,
            correctness,
            status,
            tuple(dict.fromkeys(reasons)),
            output.stdout,
            output.stderr,
        )


@dataclass
class CorrectnessExecutionAdapter:
    delegate: ExecutionAdapter
    runner: GradleCorrectnessRunner
    requests: Mapping[ExecutionState, GradleCorrectnessRequest]
    attempts: dict[ExecutionState, GradleCorrectnessRunAttempt] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        unknown = set(self.requests) - CORRECTNESS_STATES
        if unknown:
            raise ValueError(
                "correctness adapter has unsupported states: "
                + ", ".join(sorted(item.value for item in unknown))
            )
        self.requests = MappingProxyType(dict(self.requests))

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        request = self.requests.get(state)
        if request is None:
            return self.delegate.execute(state, snapshot)
        if request.run_id != snapshot.run_id:
            return StepResult("FAIL", reason_codes=("CORRECTNESS_RUN_ID_MISMATCH",))
        attempt = self.runner.run(request)
        self.attempts[state] = attempt
        return StepResult(
            attempt.status,
            output_digests=attempt.output_digests,
            reason_codes=attempt.reason_codes,
        )

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)
