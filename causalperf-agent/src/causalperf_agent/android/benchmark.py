from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from causalperf_reference.artifacts import digest

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult
from causalperf_agent.policy.model import ToolRequest

from .build import _contained, _identifier, digest_tree, sha256_file
from .process import ProcessSpec, ProcessTransport


MEASUREMENT_STATES = {
    ExecutionState.MEASURING_A1: "A1",
    ExecutionState.MEASURING_B: "B",
    ExecutionState.MEASURING_A2: "A2",
}
TRACE_SUFFIXES = (".perfetto-trace", ".trace", ".pftrace")


@dataclass(frozen=True)
class GradleBenchmarkRequest:
    """One immutable AndroidX Macrobenchmark block.

    The raw serial is local transport data.  Only ``device_serial_hash`` enters
    the policy request or durable artifacts.
    """

    run_id: str
    arm: str
    task_root: Path
    wrapper_relative_path: str
    args: tuple[str, ...]
    environment: Mapping[str, str]
    timeout_seconds: int
    output_limit_bytes: int
    result_limit_bytes: int
    result_file_limit: int
    source_relative_path: str
    source_sha256: str
    apk_relative_path: str
    apk_artifact_id: str
    apk_sha256: str
    result_root_relative_path: str
    device_serial: str = field(repr=False)
    device_serial_hash: str
    package_name: str
    partition: str
    sequence_plan_sha256: str
    environment_snapshot_id: str
    metric: str
    unit: str
    expected_iterations: int
    warmup_count: int
    max_invalid_percent: float
    predeclared_exclusion_codes: tuple[str, ...]
    benchmark_name: str | None = None
    benchmark_class: str | None = None
    stabilization_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.arm not in {"A1", "B", "A2"}:
            raise ValueError(f"unsupported measurement arm: {self.arm}")
        if self.partition not in {"DEVELOPMENT", "CALIBRATION", "QUALIFICATION", "EVALUATION"}:
            raise ValueError(f"unsupported partition: {self.partition}")
        if not self.args:
            raise ValueError("Macrobenchmark command must contain a Gradle task")
        if self.timeout_seconds < 1 or self.output_limit_bytes < 1:
            raise ValueError("benchmark process limits must be positive")
        if self.result_limit_bytes < 1 or self.result_file_limit < 2:
            raise ValueError("benchmark artifact limits must be positive")
        if self.expected_iterations < 1:
            raise ValueError("expected iterations must be positive")
        if self.warmup_count < 0:
            raise ValueError("warmup count cannot be negative")
        if self.warmup_count and not re.fullmatch(r"[a-f0-9]{64}", self.stabilization_evidence_sha256 or ""):
            raise ValueError("external stabilization evidence is required when warmup_count is non-zero")
        if not 0 <= self.max_invalid_percent <= 100:
            raise ValueError("max invalid percent must be between zero and 100")
        for name, value in (
            ("source", self.source_sha256),
            ("APK", self.apk_sha256),
            ("device", self.device_serial_hash),
            ("sequence", self.sequence_plan_sha256),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"invalid {name} SHA-256")
        if digest(self.device_serial) != self.device_serial_hash:
            raise ValueError("raw device serial does not match its sealed identity")
        if not re.fullmatch(r"AR-[A-Z0-9-]+", self.apk_artifact_id):
            raise ValueError("invalid APK artifact ID")
        if not re.fullmatch(r"ENV-[A-Z0-9-]+", self.environment_snapshot_id):
            raise ValueError("invalid environment snapshot ID")
        if not self.metric or not self.unit:
            raise ValueError("metric and unit are required")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+", self.package_name):
            raise ValueError("invalid Android package name")
        if any(not value for value in self.predeclared_exclusion_codes):
            raise ValueError("exclusion codes must be non-empty")
        if "ANDROID_SERIAL" in self.environment:
            raise ValueError("ANDROID_SERIAL is injected by the trusted Runner, not persisted in the command")
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "predeclared_exclusion_codes", tuple(self.predeclared_exclusion_codes))

    @property
    def wrapper_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.wrapper_relative_path, "Wrapper path")

    @property
    def source_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.source_relative_path, "source path")

    @property
    def apk_path(self) -> Path:
        return _contained(self.task_root.resolve(), self.apk_relative_path, "APK path")

    @property
    def result_root(self) -> Path:
        path = _contained(self.task_root.resolve(), self.result_root_relative_path, "benchmark result root")
        relative = path.relative_to(self.task_root.resolve())
        if "build" not in relative.parts:
            raise ValueError("benchmark results must be contained by a Gradle build output tree")
        for protected in (self.source_path, self.apk_path):
            if path == protected or path in protected.parents or protected in path.parents:
                raise ValueError("benchmark result root overlaps a protected input")
        return path

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
        return ToolRequest(
            "run_benchmark",
            {
                "command": self.command(),
                "device_serial_hash": self.device_serial_hash,
                "package_name": self.package_name,
                "partition": self.partition,
                "sequence_plan_sha256": self.sequence_plan_sha256,
            },
            requested_at,
        )


@dataclass(frozen=True)
class BenchmarkRunAttempt:
    request: GradleBenchmarkRequest
    status: str
    reason_codes: tuple[str, ...]
    result: dict
    measurement_set: dict | None
    artifacts: tuple[dict, ...]
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    returncode: int | None = None
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def output_digests(self) -> tuple[str, ...]:
        values = [self.result["content_sha256"]]
        values.extend(item["sha256"] for item in self.artifacts)
        if self.measurement_set is not None:
            values.append(self.measurement_set["content_sha256"])
        return tuple(dict.fromkeys(values))


class GradleBenchmarkRunner:
    def __init__(
        self,
        transport: ProcessTransport,
        *,
        clock: Callable[[], str],
        remove_tree: Callable[[Path], None] = shutil.rmtree,
    ):
        self.transport = transport
        self.clock = clock
        self.remove_tree = remove_tree

    def run(self, request: GradleBenchmarkRequest) -> BenchmarkRunAttempt:
        root = request.task_root.resolve()
        wrapper = request.wrapper_path
        source = request.source_path
        apk = request.apk_path
        result_root = request.result_root
        for label, path in (("Wrapper", wrapper), ("source", source), ("APK", apk)):
            if not path.exists() or path.is_symlink():
                raise ValueError(f"{label} input is missing or unsafe: {path}")
        if digest_tree(source) != request.source_sha256:
            raise ValueError("source identity changed before Macrobenchmark")
        if sha256_file(apk) != request.apk_sha256:
            raise ValueError("APK identity changed before Macrobenchmark")
        if result_root.exists():
            if result_root.is_symlink() or not result_root.is_dir():
                raise ValueError("stale benchmark result root is unsafe")
            self.remove_tree(result_root)
            if result_root.exists():
                raise ValueError("stale benchmark result root could not be removed")

        started_at = self.clock()
        output = self.transport.run(
            ProcessSpec(
                argv=(str(wrapper), *request.args),
                working_directory=root,
                environment={**request.environment, "ANDROID_SERIAL": request.device_serial},
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=request.output_limit_bytes,
            )
        )
        completed_at = self.clock()
        reasons: list[str] = []
        if output.timed_out:
            reasons.append("MACROBENCHMARK_TIMEOUT")
        if output.output_truncated:
            reasons.append("MACROBENCHMARK_OUTPUT_LIMIT_EXCEEDED")
        if output.returncode != 0 and not output.timed_out:
            reasons.append("MACROBENCHMARK_PROCESS_FAILED")
        if digest_tree(source) != request.source_sha256:
            reasons.append("SOURCE_CHANGED_DURING_MACROBENCHMARK")
        if not apk.exists() or apk.is_symlink() or sha256_file(apk) != request.apk_sha256:
            reasons.append("APK_CHANGED_DURING_MACROBENCHMARK")

        artifacts: tuple[dict, ...] = ()
        measurement_set = None
        if not reasons:
            try:
                raw_files = _collect_outputs(result_root, request.result_file_limit, request.result_limit_bytes)
                artifacts = _seal_outputs(request, raw_files, completed_at)
                measurement_set = _measurement_set(request, raw_files, artifacts, completed_at)
            except ValueError as error:
                reasons.append(str(error))

        status = "PASS" if not reasons else "INCONCLUSIVE"
        result = {
            "schema_version": 1,
            "run_id": request.run_id,
            "arm": request.arm,
            "partition": request.partition,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons)),
            "started_at": started_at,
            "completed_at": completed_at,
            "command_sha256": digest(request.command()),
            "device_serial_hash": request.device_serial_hash,
            "source_sha256": request.source_sha256,
            "apk_sha256": request.apk_sha256,
            "sequence_plan_sha256": request.sequence_plan_sha256,
            "artifact_digests": [item["sha256"] for item in artifacts],
        }
        if request.stabilization_evidence_sha256:
            result["stabilization_evidence_sha256"] = request.stabilization_evidence_sha256
        if measurement_set is not None:
            result["measurement_set_sha256"] = measurement_set["content_sha256"]
        result["stdout_sha256"] = _raw_digest(output.stdout)
        result["stderr_sha256"] = _raw_digest(output.stderr)
        result["content_sha256"] = digest(result, omit=("content_sha256",))
        return BenchmarkRunAttempt(
            request=request,
            status=status,
            reason_codes=tuple(result["reason_codes"]),
            result=result,
            measurement_set=measurement_set,
            artifacts=artifacts,
            stdout=output.stdout,
            stderr=output.stderr,
            returncode=output.returncode,
            timed_out=output.timed_out,
            output_truncated=output.output_truncated,
        )


def _collect_outputs(root: Path, file_limit: int, byte_limit: int) -> tuple[tuple[Path, bytes], ...]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("MACROBENCHMARK_OUTPUT_MISSING")
    selected: list[tuple[Path, bytes]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("MACROBENCHMARK_OUTPUT_SYMLINK")
        if not path.is_file():
            continue
        if path.suffix.lower() != ".json" and not path.name.lower().endswith(TRACE_SUFFIXES):
            continue
        data = path.read_bytes()
        total += len(data)
        if len(selected) + 1 > file_limit:
            raise ValueError("MACROBENCHMARK_FILE_LIMIT_EXCEEDED")
        if total > byte_limit:
            raise ValueError("MACROBENCHMARK_RESULT_LIMIT_EXCEEDED")
        selected.append((path, data))
    if not selected:
        raise ValueError("MACROBENCHMARK_OUTPUT_MISSING")
    return tuple(selected)


def _seal_outputs(
    request: GradleBenchmarkRequest,
    files: tuple[tuple[Path, bytes], ...],
    created_at: str,
) -> tuple[dict, ...]:
    result: list[dict] = []
    ids = _identifier(f"{request.run_id}-{request.arm}")
    for index, (path, data) in enumerate(files):
        is_trace = path.name.lower().endswith(TRACE_SUFFIXES)
        relative = path.relative_to(request.task_root.resolve()).as_posix()
        result.append(
            {
                "schema_version": 1,
                "id": f"AR-{'TRACE' if is_trace else 'BENCHMARK'}-{ids}-{index:03d}",
                "run_id": request.run_id,
                "partition": request.partition,
                "kind": "TRACE" if is_trace else "BENCHMARK_RESULT",
                "created_at": created_at,
                "producer": {"id": "causalperf-androidx-macrobenchmark-adapter", "version": "1"},
                "relative_path": relative,
                "media_type": "application/vnd.perfetto.trace" if is_trace else "application/json",
                "size_bytes": len(data),
                "sha256": _raw_digest(data),
                "derived_from": [request.apk_artifact_id],
                "retention": "PERMANENT" if request.partition == "CALIBRATION" else "RUN",
                "sensitivity": "AGENT_INTERNAL",
            }
        )
    return tuple(result)


def _measurement_set(
    request: GradleBenchmarkRequest,
    files: tuple[tuple[Path, bytes], ...],
    artifacts: tuple[dict, ...],
    measured_at: str,
) -> dict:
    json_files = [(path, data) for path, data in files if path.suffix.lower() == ".json"]
    trace_files = [(path, data) for path, data in files if path.name.lower().endswith(TRACE_SUFFIXES)]
    candidates: list[list[float]] = []
    for _, raw in json_files:
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("MACROBENCHMARK_JSON_INVALID") from error
        benchmarks = document.get("benchmarks") if isinstance(document, dict) else None
        if not isinstance(benchmarks, list):
            continue
        for benchmark in benchmarks:
            if not isinstance(benchmark, dict):
                continue
            if request.benchmark_name is not None and benchmark.get("name") != request.benchmark_name:
                continue
            if request.benchmark_class is not None and benchmark.get("className") != request.benchmark_class:
                continue
            metrics = benchmark.get("metrics")
            metric = metrics.get(request.metric) if isinstance(metrics, dict) else None
            runs = metric.get("runs") if isinstance(metric, dict) else None
            if not isinstance(runs, list):
                continue
            if benchmark.get("repeatIterations", len(runs)) != len(runs):
                raise ValueError("MACROBENCHMARK_ITERATION_METADATA_MISMATCH")
            parsed: list[float] = []
            for value in runs:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("MACROBENCHMARK_METRIC_INVALID")
                number = float(value)
                if not math.isfinite(number) or number < 0:
                    raise ValueError("MACROBENCHMARK_METRIC_INVALID")
                parsed.append(number)
            candidates.append(parsed)
    if len(candidates) != 1:
        raise ValueError("MACROBENCHMARK_METRIC_NOT_UNIQUE")
    values = candidates[0]
    if len(values) != request.expected_iterations:
        raise ValueError("MACROBENCHMARK_ITERATION_COUNT_MISMATCH")

    indexed_traces: dict[int, tuple[Path, bytes]] = {}
    for path, data in trace_files:
        match = re.search(r"_iter(\d+)(?:\.[^.]+)+$", path.name, re.IGNORECASE)
        if match is None:
            raise ValueError("MACROBENCHMARK_TRACE_INDEX_MISSING")
        index = int(match.group(1))
        if index in indexed_traces:
            raise ValueError("MACROBENCHMARK_TRACE_INDEX_DUPLICATE")
        indexed_traces[index] = (path, data)
    if set(indexed_traces) != set(range(request.expected_iterations)):
        raise ValueError("MACROBENCHMARK_TRACE_SET_INCOMPLETE")

    artifact_by_path = {
        item["relative_path"]: item for item in artifacts if item["kind"] == "TRACE"
    }
    ids = _identifier(f"{request.run_id}-{request.arm}-{request.metric}")
    measurements = []
    for sequence, value in enumerate(values):
        trace_path, _ = indexed_traces[sequence]
        relative = trace_path.relative_to(request.task_root.resolve()).as_posix()
        trace_artifact = artifact_by_path.get(relative)
        if trace_artifact is None:
            raise ValueError("MACROBENCHMARK_TRACE_ARTIFACT_MISSING")
        measurements.append(
            {
                "id": f"M-{ids}-{sequence:03d}",
                "sequence": sequence,
                "value": value,
                "measured_at": measured_at,
                "environment_snapshot_id": request.environment_snapshot_id,
                "source_sha256": request.source_sha256,
                "apk_sha256": request.apk_sha256,
                "trace_sha256": trace_artifact["sha256"],
                "included": True,
            }
        )
    result = {
        "schema_version": 1,
        "id": f"MS-{ids}",
        "run_id": request.run_id,
        "partition": request.partition,
        "arm": request.arm,
        "metric": request.metric,
        "unit": request.unit,
        "measurements": measurements,
        "policy": {
            "warmup_count": request.warmup_count,
            "minimum_included": request.expected_iterations,
            "max_invalid_percent": request.max_invalid_percent,
            "predeclared_exclusion_codes": list(request.predeclared_exclusion_codes),
        },
    }
    result["content_sha256"] = digest(result, omit=("content_sha256",))
    return result


def _raw_digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


@dataclass
class GradleBenchmarkExecutionAdapter:
    delegate: ExecutionAdapter
    runner: GradleBenchmarkRunner
    requests: Mapping[ExecutionState, GradleBenchmarkRequest]
    attempts: dict[ExecutionState, BenchmarkRunAttempt] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        unknown = set(self.requests) - set(MEASUREMENT_STATES)
        if unknown:
            raise ValueError(f"unsupported benchmark states: {sorted(item.value for item in unknown)}")

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        request = self.requests.get(state)
        if request is None:
            return self.delegate.execute(state, snapshot)
        if request.run_id != snapshot.run_id:
            return StepResult("FAIL", reason_codes=("BENCHMARK_RUN_ID_MISMATCH",))
        if request.arm != MEASUREMENT_STATES[state]:
            return StepResult("FAIL", reason_codes=("BENCHMARK_ARM_MISMATCH",))
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
