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

from .process import ProcessSpec, ProcessTransport


BUILD_STATES = {
    ExecutionState.BUILDING_BASELINE,
    ExecutionState.BUILDING_TREATMENT,
}


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def digest_tree(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"source root is not a real directory: {root}")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"source symlink forbidden: {path}")
        if not path.is_file():
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return digest(entries)


def _contained(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe {label}: {relative}")
    physical_root = root.resolve()
    unresolved = physical_root / candidate
    current = physical_root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains forbidden symlink: {relative}")
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(physical_root)
    except ValueError as error:
        raise ValueError(f"{label} escapes task root: {relative}") from error
    return resolved


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]", "-", value.upper()).strip("-")
    if not normalized:
        raise ValueError("identifier cannot be normalized")
    return normalized


@dataclass(frozen=True)
class GradleBuildRequest:
    run_id: str
    role: str
    task_root: Path
    wrapper_relative_path: str
    args: tuple[str, ...]
    environment: Mapping[str, str]
    timeout_seconds: int
    output_limit_bytes: int
    source_relative_path: str
    apk_relative_path: str
    source_artifact_id: str
    toolchain: Mapping[str, str]
    partition: str = "DEVELOPMENT"

    def __post_init__(self) -> None:
        if self.role not in {"BASELINE", "TREATMENT", "RESTORED"}:
            raise ValueError(f"unsupported build role: {self.role}")
        if self.partition not in {"DEVELOPMENT", "CALIBRATION", "QUALIFICATION", "EVALUATION"}:
            raise ValueError(f"unsupported partition: {self.partition}")
        if not self.args or self.args[0] != "clean":
            raise ValueError("Android reproducibility build must begin with clean")
        if not re.fullmatch(r"AR-[A-Z0-9-]+", self.source_artifact_id):
            raise ValueError("invalid source artifact ID")
        if not self.toolchain or any(not key or not value for key, value in self.toolchain.items()):
            raise ValueError("toolchain must contain non-empty string identities")
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "toolchain", MappingProxyType(dict(self.toolchain)))

    @property
    def wrapper_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.wrapper_relative_path, "Wrapper path")

    @property
    def source_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.source_relative_path, "source path")

    @property
    def apk_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.apk_relative_path, "APK path")

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

    def tool_request(self, requested_at: str) -> ToolRequest:
        return ToolRequest("build_variant", self.command(), requested_at)


@dataclass(frozen=True)
class GradleBuildAttempt:
    request: GradleBuildRequest
    source_sha256: str
    build_result: dict
    apk_artifact: dict | None
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    returncode: int | None = None
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def status(self) -> str:
        return self.build_result["status"]

    @property
    def output_digests(self) -> tuple[str, ...]:
        values = [self.build_result["content_sha256"]]
        if self.apk_artifact is not None:
            values.append(self.apk_artifact["sha256"])
        return tuple(values)


class GradleBuildAdapter:
    def __init__(
        self,
        transport: ProcessTransport,
        *,
        clock: Callable[[], str],
        path_exists: Callable[[Path], bool] = Path.exists,
        remove_path: Callable[[Path], None] = Path.unlink,
    ):
        self.transport = transport
        self.clock = clock
        self.path_exists = path_exists
        self.remove_path = remove_path

    def build(self, request: GradleBuildRequest) -> GradleBuildAttempt:
        root = request.task_root.resolve()
        wrapper = request.wrapper_path
        source = request.source_path
        apk = request.apk_path
        if not self.path_exists(wrapper):
            raise ValueError(f"Gradle Wrapper is missing: {request.wrapper_relative_path}")
        if not self.path_exists(source):
            raise ValueError(f"source root is missing: {request.source_relative_path}")
        # A clean build must prove the declared output belongs to this attempt,
        # rather than accidentally accepting a stale APK from a previous run.
        if self.path_exists(apk):
            self.remove_path(apk)
            if self.path_exists(apk):
                raise ValueError(f"stale APK could not be removed: {request.apk_relative_path}")
        source_before = digest_tree(source)
        started_at = self.clock()
        output = self.transport.run(
            ProcessSpec(
                argv=(str(wrapper), *request.args),
                working_directory=root,
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=request.output_limit_bytes,
            )
        )
        completed_at = self.clock()
        reason_codes: list[str] = []
        status = "PASS"
        apk_artifact = None
        if output.timed_out:
            status = "INCONCLUSIVE"
            reason_codes.append("GRADLE_TIMEOUT")
        elif output.output_truncated:
            status = "INCONCLUSIVE"
            reason_codes.append("GRADLE_OUTPUT_LIMIT_EXCEEDED")
        elif output.returncode != 0:
            status = "FAIL"
            reason_codes.append("GRADLE_BUILD_FAILED")
        elif not self.path_exists(apk):
            status = "INCONCLUSIVE"
            reason_codes.append("APK_NOT_PRODUCED")

        source_after = digest_tree(source)
        if source_after != source_before:
            status = "FAIL"
            reason_codes.append("SOURCE_CHANGED_DURING_BUILD")

        ids = _identifier(f"{request.run_id}-{request.role}")
        if status == "PASS":
            apk_digest = sha256_file(apk)
            apk_artifact = {
                "schema_version": 1,
                "id": f"AR-APK-{ids}",
                "run_id": request.run_id,
                "partition": request.partition,
                "kind": "APK",
                "created_at": completed_at,
                "producer": {"id": "causalperf-gradle-build-adapter", "version": "1"},
                "relative_path": request.apk_relative_path.replace("\\", "/"),
                "media_type": "application/vnd.android.package-archive",
                "size_bytes": apk.stat().st_size,
                "sha256": apk_digest,
                "derived_from": [request.source_artifact_id],
                "retention": "RUN",
                "sensitivity": "AGENT_INTERNAL",
            }

        build_result = {
            "schema_version": 1,
            "id": f"BR-{ids}",
            "run_id": request.run_id,
            "partition": request.partition,
            "started_at": started_at,
            "completed_at": completed_at,
            "source_artifact_id": request.source_artifact_id,
            "command_request_sha256": digest(request.command()),
            "status": status,
            "toolchain": dict(request.toolchain),
            "output_artifact_ids": [apk_artifact["id"]] if apk_artifact else [],
        }
        if reason_codes:
            build_result["reason_codes"] = list(dict.fromkeys(reason_codes))
        build_result["content_sha256"] = digest(build_result, omit=("content_sha256",))
        return GradleBuildAttempt(
            request,
            source_before,
            build_result,
            apk_artifact,
            output.stdout,
            output.stderr,
            output.returncode,
            output.timed_out,
            output.output_truncated,
        )


@dataclass
class GradleBuildExecutionAdapter:
    delegate: ExecutionAdapter
    builder: GradleBuildAdapter
    requests: Mapping[ExecutionState, GradleBuildRequest]
    attempts: dict[ExecutionState, GradleBuildAttempt] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        unknown = set(self.requests) - BUILD_STATES
        if unknown:
            raise ValueError(f"Gradle build adapter has unsupported states: {sorted(item.value for item in unknown)}")

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        request = self.requests.get(state)
        if request is None:
            return self.delegate.execute(state, snapshot)
        if request.run_id != snapshot.run_id:
            return StepResult("FAIL", reason_codes=("BUILD_RUN_ID_MISMATCH",))
        attempt = self.builder.build(request)
        self.attempts[state] = attempt
        if attempt.status != "PASS":
            return StepResult(
                attempt.status,
                output_digests=(attempt.build_result["content_sha256"],),
                reason_codes=tuple(attempt.build_result.get("reason_codes", [])),
            )
        return StepResult("PASS", output_digests=attempt.output_digests)

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)
