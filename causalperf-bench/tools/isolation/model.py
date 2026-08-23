from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import jsonschema


SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"
FORMAT_CHECKER = jsonschema.FormatChecker()


def canonical_digest(value: dict, *, omit: tuple[str, ...] = ()) -> str:
    filtered = {key: item for key, item in value.items() if key not in omit}
    encoded = json.dumps(filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validator(name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


POLICY_VALIDATOR = _validator("isolation-policy.schema.json")
CANARY_VALIDATOR = _validator("private-canary-set.schema.json")
REPORT_VALIDATOR = _validator("isolation-report.schema.json")
RUN_VALIDATOR = _validator("isolation-run.schema.json")
POLICY_V1_VALIDATOR = _validator("archive/isolation-policy.v1.schema.json")
RUN_V1_VALIDATOR = _validator("archive/isolation-run.v1.schema.json")


class IsolationContractError(ValueError):
    pass


def _validate_sealed(document: dict, validator: jsonschema.Draft202012Validator, label: str) -> None:
    try:
        validator.validate(document)
    except jsonschema.ValidationError as error:
        raise IsolationContractError(f"{label} schema violation: {error.message}") from error
    actual = canonical_digest(document, omit=("content_sha256",))
    if document["content_sha256"] != actual:
        raise IsolationContractError(f"{label} content digest mismatch")


def _upgrade_sealed(document: dict, *, current: jsonschema.Draft202012Validator,
                    previous: jsonschema.Draft202012Validator, label: str) -> tuple[dict, str]:
    source = copy.deepcopy(document)
    source_digest = source.get("content_sha256", "")
    version = source.get("schema_version")
    if version == 1:
        _validate_sealed(source, previous, label)
        source["schema_version"] = 2
        source["content_sha256"] = canonical_digest(source, omit=("content_sha256",))
    elif version != 2:
        raise IsolationContractError(f"{label} schema violation: unsupported schema_version")
    _validate_sealed(source, current, label)
    return source, source_digest


def _within(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


class IsolationPolicy:
    def __init__(self, document: dict):
        upgraded, source_digest = _upgrade_sealed(
            document, current=POLICY_VALIDATOR, previous=POLICY_V1_VALIDATOR,
            label="isolation policy",
        )
        writable = [PurePosixPath(item) for item in upgraded["writable_paths"]]
        protected = [PurePosixPath(item) for item in upgraded["protected_paths"]]
        if any(_within(left, right) or _within(right, left) for left in writable for right in protected):
            raise IsolationContractError("writable and protected paths overlap")
        self._document = upgraded
        self._source_digest = source_digest

    @property
    def run_id(self) -> str:
        return self._document["run_id"]

    @property
    def digest(self) -> str:
        return self._document["content_sha256"]

    @property
    def source_digest(self) -> str:
        return self._source_digest

    def get(self, key: str):
        return copy.deepcopy(self._document[key])


class PrivateCanarySet:
    def __init__(self, document: dict):
        _validate_sealed(document, CANARY_VALIDATOR, "private canary set")
        values = [item["value"] for item in document["canaries"]]
        identifiers = [item["id"] for item in document["canaries"]]
        if len(values) != len(set(values)) or len(identifiers) != len(set(identifiers)):
            raise IsolationContractError("private canaries are not unique")
        self._document = copy.deepcopy(document)

    @property
    def task_id(self) -> str:
        return self._document["task_id"]

    @property
    def task_version(self) -> str:
        return self._document["task_version"]

    @property
    def digest(self) -> str:
        return self._document["content_sha256"]

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(item["value"] for item in self._document["canaries"])


@dataclass(frozen=True)
class CommandSpec:
    executable: str
    args: tuple[str, ...]
    working_directory: Path
    environment: dict[str, str]

    def validate(self, policy: IsolationPolicy, *, evaluator: bool) -> None:
        if not _is_absolute(self.executable) or self.executable not in policy.get("allowed_executables"):
            raise IsolationContractError("command executable is not allowlisted")
        if not _is_absolute(str(self.working_directory)):
            raise IsolationContractError("command working directory must be absolute")
        allowed_key = "evaluator_environment_keys" if evaluator else "agent_environment_keys"
        if set(self.environment) - set(policy.get(allowed_key)):
            raise IsolationContractError("command environment contains a non-allowlisted key")
        if any("\x00" in item or "\n" in item for item in self.args):
            raise IsolationContractError("command argument contains a forbidden control character")

    @classmethod
    def from_document(cls, document: dict) -> "CommandSpec":
        return cls(
            executable=document["executable"],
            args=tuple(document["args"]),
            working_directory=Path(document["working_directory"]),
            environment=copy.deepcopy(document["environment"]),
        )


@dataclass(frozen=True)
class IsolationRunSpec:
    task_id: str
    task_version: str
    public_source: Path
    private_evaluator: Path
    run_root: Path
    agent_command: CommandSpec
    evaluator_command: CommandSpec

    @classmethod
    def from_document(cls, document: dict, policy: IsolationPolicy,
                      canaries: PrivateCanarySet) -> "IsolationRunSpec":
        upgraded, _ = _upgrade_sealed(
            document, current=RUN_VALIDATOR, previous=RUN_V1_VALIDATOR,
            label="isolation run",
        )
        if (
            upgraded["run_id"] != policy.run_id
            or upgraded["policy_sha256"] not in {policy.digest, policy.source_digest}
        ):
            raise IsolationContractError("isolation run does not bind the active policy")
        if (
            upgraded["canary_set_sha256"] != canaries.digest
            or upgraded["task_id"] != canaries.task_id
            or upgraded["task_version"] != canaries.task_version
        ):
            raise IsolationContractError("isolation run does not bind the active canary set")
        return cls(
            task_id=upgraded["task_id"],
            task_version=upgraded["task_version"],
            public_source=Path(upgraded["public_source"]),
            private_evaluator=Path(upgraded["private_evaluator"]),
            run_root=Path(upgraded["run_root"]),
            agent_command=CommandSpec.from_document(upgraded["agent_command"]),
            evaluator_command=CommandSpec.from_document(upgraded["evaluator_command"]),
        )


def seal_report(report: dict) -> dict:
    value = copy.deepcopy(report)
    value["content_sha256"] = canonical_digest(value, omit=("content_sha256",))
    try:
        REPORT_VALIDATOR.validate(value)
    except jsonschema.ValidationError as error:
        raise IsolationContractError(f"isolation report schema violation: {error.message}") from error
    return value


def _is_absolute(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
