from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import jsonschema
from causalperf_reference.artifacts import ContractError, digest, verify_content_digest


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas"
SHARED_SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "shared" / "schemas"
RUNTIME_POLICY_VALIDATOR = jsonschema.Draft202012Validator(
    json.loads((SCHEMA_ROOT / "runtime-policy.schema.json").read_text(encoding="utf-8"))
)
TOOL_CONTRACT_VALIDATOR = jsonschema.Draft202012Validator(
    json.loads((SCHEMA_ROOT / "tool-contract.schema.json").read_text(encoding="utf-8"))
)
APPROVAL_RECORD_VALIDATOR = jsonschema.Draft202012Validator(
    json.loads((SHARED_SCHEMA_ROOT / "approval-record.schema.json").read_text(encoding="utf-8")),
    format_checker=jsonschema.FormatChecker(),
)


@dataclass(frozen=True, init=False)
class ToolRequest:
    tool_id: str
    _arguments: dict = field(repr=False)
    requested_at: str
    authorization_at: str | None = None
    _request_sha256: str = field(repr=False)

    def __init__(self, tool_id: str, arguments: dict, requested_at: str,
                 authorization_at: str | None = None):
        sealed_arguments = copy.deepcopy(arguments)
        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "_arguments", sealed_arguments)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "authorization_at", authorization_at)
        object.__setattr__(self, "_request_sha256", digest({
            "tool_id": tool_id,
            "arguments": sealed_arguments,
        }))

    @property
    def arguments(self) -> dict:
        return copy.deepcopy(self._arguments)

    @property
    def request_sha256(self) -> str:
        return self._request_sha256


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    risk: str
    request_sha256: str
    reason_codes: tuple[str, ...] = ()
    approval_id: str | None = None
    budget_delta: tuple[tuple[str, int], ...] = ()
    authorization_at: str | None = None

    def to_dict(self) -> dict:
        value = {
            "status": self.status, "risk": self.risk,
            "request_sha256": self.request_sha256,
            "reason_codes": list(self.reason_codes),
            "budget_delta": dict(self.budget_delta),
        }
        if self.approval_id:
            value["approval_id"] = self.approval_id
        if self.authorization_at:
            value["authorization_at"] = self.authorization_at
        return value


class RuntimePolicy:
    """Immutable, sealed policy view owned by the runner rather than the model."""

    def __init__(self, document: dict):
        try:
            RUNTIME_POLICY_VALIDATOR.validate(document)
        except jsonschema.ValidationError as error:
            raise ContractError(f"runtime policy schema violation: {error.message}") from error
        verify_content_digest(document)
        required = {
            "schema_version", "id", "run_id", "network", "readable_paths", "writable_paths",
            "protected_paths", "allowed_executables", "allowed_working_directories",
            "allowed_environment_keys", "device_serial_hash", "package_name", "allowed_partitions",
            "task_approved_risks", "allow_external_publication", "budgets", "content_sha256",
        }
        missing = required - document.keys()
        if missing:
            raise ContractError(f"runtime policy missing: {sorted(missing)}")
        if document["schema_version"] != 1 or document["network"] != "denied" or document["allow_external_publication"] is not False:
            raise ContractError("unsupported runtime policy capability")
        path_fields = ("readable_paths", "writable_paths", "protected_paths", "allowed_working_directories")
        for field_name in path_fields:
            paths = document[field_name]
            if not isinstance(paths, list) or not paths:
                raise ContractError(f"runtime policy {field_name} is invalid")
            for path in paths:
                candidate = PurePosixPath(path) if isinstance(path, str) and path else None
                if candidate is None or candidate.is_absolute() or ".." in candidate.parts:
                    raise ContractError(f"runtime policy {field_name} contains an unsafe path")
        budget_keys = {"tool_calls", "wall_time_seconds", "experiments", "patch_files", "patch_lines", "output_bytes"}
        if set(document["budgets"]) != budget_keys or any(not isinstance(value, int) or value < 1 for value in document["budgets"].values()):
            raise ContractError("runtime policy budgets are invalid")
        for writable in document["writable_paths"]:
            for protected in document["protected_paths"]:
                left, right = PurePosixPath(writable), PurePosixPath(protected)
                if left == right or left in right.parents or right in left.parents:
                    raise ContractError("runtime policy writable and protected paths overlap")
        self._document = copy.deepcopy(document)

    @property
    def digest(self) -> str:
        return self._document["content_sha256"]

    @property
    def run_id(self) -> str:
        return self._document["run_id"]

    def get(self, key: str):
        return copy.deepcopy(self._document[key])

    def to_document(self) -> dict:
        return copy.deepcopy(self._document)


def validate_tool_request(request: ToolRequest) -> None:
    try:
        TOOL_CONTRACT_VALIDATOR.validate({
            "schema_version": 1,
            "tool_id": request.tool_id,
            "request": request.arguments,
        })
    except jsonschema.ValidationError as error:
        raise ContractError(f"tool request schema violation: {error.message}") from error


def validate_approval_record(approval: dict) -> None:
    try:
        APPROVAL_RECORD_VALIDATOR.validate(approval)
    except jsonschema.ValidationError as error:
        raise ContractError(f"approval record schema violation: {error.message}") from error
