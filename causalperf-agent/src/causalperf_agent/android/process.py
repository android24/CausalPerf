from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str] = field(repr=False)
    timeout_seconds: int
    output_limit_bytes: int

    def __post_init__(self) -> None:
        if not self.argv or any(not isinstance(item, str) or not item for item in self.argv):
            raise ValueError("process argv must contain non-empty strings")
        if self.timeout_seconds < 1:
            raise ValueError("process timeout must be positive")
        if self.output_limit_bytes < 1:
            raise ValueError("process output limit must be positive")
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))


@dataclass(frozen=True)
class ProcessOutput:
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    output_truncated: bool = False


class ProcessTransport(Protocol):
    def run(self, spec: ProcessSpec) -> ProcessOutput: ...


class SubprocessTransport:
    """Exact-argv subprocess transport; it never invokes a shell."""

    def run(self, spec: ProcessSpec) -> ProcessOutput:
        try:
            completed = subprocess.run(
                list(spec.argv),
                cwd=spec.working_directory,
                env=dict(spec.environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=spec.timeout_seconds,
                check=False,
                shell=False,
            )
            stdout, stderr, truncated = _bounded_output(
                completed.stdout, completed.stderr, spec.output_limit_bytes
            )
            return ProcessOutput(completed.returncode, stdout, stderr, False, truncated)
        except subprocess.TimeoutExpired as error:
            stdout, stderr, truncated = _bounded_output(
                _bytes(error.stdout), _bytes(error.stderr), spec.output_limit_bytes
            )
            return ProcessOutput(None, stdout, stderr, True, truncated)


def _bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")


def _bounded_output(stdout: bytes, stderr: bytes, limit: int) -> tuple[bytes, bytes, bool]:
    combined = len(stdout) + len(stderr)
    if combined <= limit:
        return stdout, stderr, False
    stdout_budget = min(len(stdout), limit)
    bounded_stdout = stdout[:stdout_budget]
    bounded_stderr = stderr[: max(0, limit - stdout_budget)]
    return bounded_stdout, bounded_stderr, True
