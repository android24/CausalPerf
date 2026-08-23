from __future__ import annotations

import json
import os
from pathlib import Path

from causalperf_reference.artifacts import digest


class CheckpointError(ValueError):
    pass


class FileRunStore:
    """Atomic local checkpoint store for one run; not an artifact repository."""

    def __init__(self, run_directory: str | Path):
        self.run_directory = Path(run_directory)
        self.path = self.run_directory / "execution-checkpoint.json"

    def save(self, snapshot: dict, ledger_events: list[dict]) -> None:
        self.run_directory.mkdir(parents=True, exist_ok=True)
        envelope = {"schema_version": 1, "snapshot": snapshot, "ledger_events": ledger_events}
        envelope["content_sha256"] = digest(envelope, omit=("content_sha256",))
        temporary = self.run_directory / ".execution-checkpoint.tmp"
        data = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def load(self) -> tuple[dict, list[dict]]:
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        expected = envelope.get("content_sha256")
        actual = digest(envelope, omit=("content_sha256",))
        if expected != actual:
            raise CheckpointError("checkpoint digest mismatch")
        if envelope.get("schema_version") != 1:
            raise CheckpointError("unsupported checkpoint schema version")
        return envelope["snapshot"], envelope["ledger_events"]
