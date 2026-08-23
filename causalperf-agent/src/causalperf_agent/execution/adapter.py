from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import ExecutionSnapshot, ExecutionState, StepResult


class TransportError(RuntimeError):
    """A device/process transport failed before a trustworthy completion."""


@dataclass(frozen=True)
class RecoveryObservation:
    workspace: str
    device: str
    source_matches_baseline: bool
    owned_processes_running: bool = False


class ExecutionAdapter(Protocol):
    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult: ...

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation: ...

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult: ...
