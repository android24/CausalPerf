from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Callable

from .artifacts import ContractError, digest


BUNDLE_VERSION = "0.6.0"
SCHEMA_PATTERNS = (
    "shared/schemas/*.json",
    "causalperf-agent/schemas/**/*.schema.json",
    "causalperf-bench/schemas/**/*.schema.json",
)

Migration = Callable[[dict], dict]
def _migrate_isolation_contract_v1_to_v2(document: dict) -> dict:
    """Opt into cross-platform absolute paths and Windows Sandbox identity."""
    result = copy.deepcopy(document)
    result["schema_version"] = 2
    if "content_sha256" in result:
        result["content_sha256"] = digest(result, omit=("content_sha256",))
    return result


def _migrate_task_reproduction_v1_to_v2(document: dict) -> dict:
    result = copy.deepcopy(document)
    result["schema_version"] = 2
    experiment_evidence = {
        "ENVIRONMENT_SNAPSHOT", "A1_B_A2_MEASUREMENTS", "TRACES",
        "MECHANISM_EVIDENCE", "VARIANCE_REPORT",
    }
    qualification_only = {"INDEPENDENT_REPLAY", "LEAKAGE_REVIEW"}
    for artifact in result.get("artifacts", []):
        if artifact.get("kind") in qualification_only:
            partition_name = "QUALIFICATION"
        elif artifact.get("kind") in experiment_evidence:
            partition_name = "CALIBRATION"
        else:
            partition_name = "DEVELOPMENT"
        artifact["partition"] = partition_name
        if artifact.get("status") != "MISSING" and "sha256" in artifact:
            partition = result["partitions"][partition_name]
            if partition["status"] == "NOT_STARTED":
                partition["status"] = "OPEN"
            if artifact["sha256"] not in partition["artifact_sha256s"]:
                partition["artifact_sha256s"].append(artifact["sha256"])
    return result


MIGRATIONS: dict[tuple[str, int], tuple[int, Migration]] = {
    ("https://causalperf.dev/bench/schemas/isolation-policy.schema.json", 1):
        (2, _migrate_isolation_contract_v1_to_v2),
    ("https://causalperf.dev/bench/schemas/isolation-report.schema.json", 1):
        (2, _migrate_isolation_contract_v1_to_v2),
    ("https://causalperf.dev/bench/schemas/isolation-run.schema.json", 1):
        (2, _migrate_isolation_contract_v1_to_v2),
    ("https://causalperf.dev/schemas/task-reproduction-package.schema.json", 1):
        (2, _migrate_task_reproduction_v1_to_v2),
}


def _schema_paths(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for pattern in SCHEMA_PATTERNS:
        paths.update(root.glob(pattern))
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _document_version(schema: dict, path: Path) -> int:
    value = schema.get("properties", {}).get("schema_version", {}).get("const")
    if not isinstance(value, int) or value < 1:
        raise ContractError(f"schema lacks an integer schema_version const: {path}")
    return value


def build_bundle(root: Path, *, bundle_version: str = BUNDLE_VERSION) -> dict:
    entries = []
    schema_versions: set[tuple[str, int]] = set()
    for path in _schema_paths(root):
        raw = path.read_bytes()
        schema = json.loads(raw)
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str):
            raise ContractError(f"schema lacks $id: {path}")
        document_version = _document_version(schema, path)
        key = (schema_id, document_version)
        if key in schema_versions:
            raise ContractError(
                f"duplicate schema $id/version: {schema_id} v{document_version}"
            )
        schema_versions.add(key)
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "schema_id": schema_id,
            "document_schema_version": document_version,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    registry = [
        {"schema_id": schema_id, "from_version": source, "to_version": target,
         "function": function.__name__, "information_loss": []}
        for (schema_id, source), (target, function) in sorted(MIGRATIONS.items())
    ]
    bundle = {
        "schema_version": 1,
        "bundle_id": "causalperf-contracts",
        "bundle_version": bundle_version,
        "json_schema_draft": "https://json-schema.org/draft/2020-12/schema",
        "entries": entries,
        "migration_registry": registry,
    }
    bundle["bundle_sha256"] = digest(bundle, omit=("bundle_sha256",))
    return bundle


def verify_bundle(root: Path, bundle: dict) -> None:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ContractError(f"unsupported schema bundle version: {bundle.get('bundle_version')}")
    if bundle.get("bundle_sha256") != digest(bundle, omit=("bundle_sha256",)):
        raise ContractError("schema bundle digest mismatch")
    expected = build_bundle(root, bundle_version=bundle.get("bundle_version", ""))
    if bundle != expected:
        raise ContractError("schema bundle does not match repository schemas or migration registry")


def migrate(document: dict, *, schema_id: str, target_version: int) -> dict:
    """Pure, fail-closed migration; current-version input is a no-op copy."""
    result = copy.deepcopy(document)
    current = result.get("schema_version")
    if not isinstance(current, int):
        raise ContractError("document lacks an integer schema_version")
    if current > target_version:
        raise ContractError(f"cannot downgrade {schema_id} from v{current} to v{target_version}")
    while current < target_version:
        migration = MIGRATIONS.get((schema_id, current))
        if migration is None:
            raise ContractError(f"no migration registered for {schema_id} v{current}")
        next_version, function = migration
        if next_version != current + 1:
            raise ContractError(f"non-contiguous migration for {schema_id} v{current}")
        result = function(copy.deepcopy(result))
        if result.get("schema_version") != next_version:
            raise ContractError(f"migration did not produce {schema_id} v{next_version}")
        current = next_version
    return result


def load_bundle(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
