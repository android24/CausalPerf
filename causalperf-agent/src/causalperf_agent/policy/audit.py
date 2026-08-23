from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import jsonschema
from causalperf_reference.artifacts import digest


TOOL_CALL_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / "shared" / "schemas" / "tool-call.schema.json")
    .read_text(encoding="utf-8")
)
TOOL_CALL_VALIDATOR = jsonschema.Draft202012Validator(
    TOOL_CALL_SCHEMA,
    format_checker=jsonschema.FormatChecker(),
)


class AuditStoreError(ValueError):
    pass


class FileToolCallAuditStore:
    """Atomic, content-addressed ToolCall log for one run."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, records: list[dict]) -> None:
        for record in records:
            try:
                TOOL_CALL_VALIDATOR.validate(record)
            except jsonschema.ValidationError as error:
                raise AuditStoreError(f"invalid ToolCall record: {error.message}") from error
        identifiers = [record["id"] for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise AuditStoreError("duplicate ToolCall ID")
        envelope = {
            "schema_version": 1,
            "tool_calls": copy.deepcopy(records),
        }
        envelope["content_sha256"] = digest(envelope, omit=("content_sha256",))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        data = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        if envelope.get("schema_version") != 1:
            raise AuditStoreError("unsupported ToolCall audit version")
        if envelope.get("content_sha256") != digest(envelope, omit=("content_sha256",)):
            raise AuditStoreError("ToolCall audit digest mismatch")
        records = envelope.get("tool_calls")
        if not isinstance(records, list):
            raise AuditStoreError("ToolCall audit records are missing")
        for record in records:
            try:
                TOOL_CALL_VALIDATOR.validate(record)
            except jsonschema.ValidationError as error:
                raise AuditStoreError(f"invalid persisted ToolCall: {error.message}") from error
        identifiers = [record["id"] for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise AuditStoreError("duplicate persisted ToolCall ID")
        return copy.deepcopy(records)
