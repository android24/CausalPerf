#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import jsonschema


LIFECYCLES = ("DRAFT", "IMPLEMENTED", "CALIBRATED", "QUALIFIED", "FROZEN")
IMPLEMENTED = {
    "TASK_SPEC", "SOURCE", "PUBLIC_TASK", "CORRECTNESS_TESTS",
    "BENCHMARK_SCENARIO", "GROUND_TRUTH", "REFERENCE_PATCH",
}
EXPERIMENT_EVIDENCE = {
    "ENVIRONMENT_SNAPSHOT", "A1_B_A2_MEASUREMENTS", "TRACES",
    "MECHANISM_EVIDENCE", "VARIANCE_REPORT",
}
QUALIFICATION_ONLY = {"INDEPENDENT_REPLAY", "LEAKAGE_REVIEW"}
V1_SCHEMA = Path(__file__).parents[1] / "schemas" / "archive" / "task-reproduction-package.v1.schema.json"


class ReproductionError(ValueError):
    pass


def digest_path(path: Path) -> str:
    if path.is_symlink():
        raise ReproductionError(f"symlink artifact forbidden: {path}")
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise ReproductionError(f"artifact path missing: {path}")
    hasher = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        if item.is_symlink():
            raise ReproductionError(f"symlink artifact forbidden: {item}")
        relative = item.relative_to(path).as_posix().encode()
        item_digest = hashlib.sha256(item.read_bytes()).hexdigest().encode()
        hasher.update(relative + b"\0" + item_digest + b"\n")
    return hasher.hexdigest()


def migrate_v1(document: dict) -> dict:
    """Conservatively bind legacy artifact kinds to their original role."""
    if document.get("schema_version") != 1:
        return document
    legacy_schema = json.loads(V1_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(document, legacy_schema, format_checker=jsonschema.FormatChecker())
    migrated = json.loads(json.dumps(document))
    migrated["schema_version"] = 2
    for artifact in migrated["artifacts"]:
        if artifact["kind"] in QUALIFICATION_ONLY:
            artifact["partition"] = "QUALIFICATION"
        elif artifact["kind"] in EXPERIMENT_EVIDENCE:
            artifact["partition"] = "CALIBRATION"
        else:
            artifact["partition"] = "DEVELOPMENT"
        if artifact["status"] != "MISSING":
            partition = migrated["partitions"][artifact["partition"]]
            if partition["status"] == "NOT_STARTED":
                partition["status"] = "OPEN"
            if artifact["sha256"] not in partition["artifact_sha256s"]:
                partition["artifact_sha256s"].append(artifact["sha256"])
    return migrated


def required_artifacts(lifecycle: str) -> dict[str, set[str]]:
    required = {"DEVELOPMENT": {"TASK_SPEC"}}
    if LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("IMPLEMENTED"):
        required["DEVELOPMENT"] = set(IMPLEMENTED)
    if LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("CALIBRATED"):
        required["CALIBRATION"] = set(EXPERIMENT_EVIDENCE)
    if LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("QUALIFIED"):
        required["QUALIFICATION"] = set(EXPERIMENT_EVIDENCE | QUALIFICATION_ONLY)
    return required


def verify_readiness(document: dict, by_key: dict[tuple[str, str], dict],
                     lifecycle: str) -> None:
    required = required_artifacts(lifecycle)
    missing: list[str] = []
    unverified: list[str] = []
    for partition, kinds in required.items():
        for kind in sorted(kinds):
            artifact = by_key.get((partition, kind))
            label = f"{partition}/{kind}"
            if artifact is None or artifact["status"] == "MISSING":
                missing.append(label)
            elif LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("CALIBRATED") and artifact["status"] != "VERIFIED":
                unverified.append(label)
    if missing:
        raise ReproductionError(f"{lifecycle} package incomplete: {', '.join(missing)}")
    if unverified:
        raise ReproductionError(f"{lifecycle} artifacts not verified: {', '.join(unverified)}")

    partitions = document["partitions"]
    if LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("CALIBRATED"):
        if partitions["CALIBRATION"]["status"] != "SEALED":
            raise ReproductionError("CALIBRATION partition must be SEALED")
    if LIFECYCLES.index(lifecycle) >= LIFECYCLES.index("QUALIFIED"):
        if partitions["QUALIFICATION"]["status"] != "SEALED":
            raise ReproductionError("QUALIFICATION partition must be SEALED")
    if lifecycle == "FROZEN" and partitions["DEVELOPMENT"]["status"] != "SEALED":
        raise ReproductionError("DEVELOPMENT partition must be SEALED")


def validate_manifest(task_dir: Path, schema: dict, *, require_lifecycle: str | None = None) -> dict:
    manifest_path = task_dir / "reproduction.json"
    document = migrate_v1(json.loads(manifest_path.read_text(encoding="utf-8")))
    jsonschema.validate(document, schema, format_checker=jsonschema.FormatChecker())
    by_key: dict[tuple[str, str], dict] = {}
    declared_digests: dict[str, set[str]] = {
        partition: set(content["artifact_sha256s"])
        for partition, content in document["partitions"].items()
    }
    for artifact in document["artifacts"]:
        kind = artifact["kind"]
        partition = artifact["partition"]
        key = (partition, kind)
        if key in by_key:
            raise ReproductionError(f"duplicate partition artifact: {partition}/{kind}")
        by_key[key] = artifact
        if artifact["status"] == "MISSING":
            if "relative_path" in artifact or "sha256" in artifact:
                raise ReproductionError(f"missing artifact carries material: {partition}/{kind}")
            continue
        target = (task_dir / artifact["relative_path"]).resolve()
        try:
            target.relative_to(task_dir.resolve())
        except ValueError as error:
            raise ReproductionError(f"artifact escapes task: {kind}") from error
        if digest_path(target) != artifact["sha256"]:
            raise ReproductionError(f"artifact digest mismatch: {partition}/{kind}")
        if artifact["sha256"] not in declared_digests[partition]:
            raise ReproductionError(f"artifact absent from partition registry: {partition}/{kind}")
        relative = target.relative_to(task_dir.resolve()).parts
        if artifact["visibility"] == "PUBLIC" and relative[0] == "private-evaluator":
            raise ReproductionError(f"public artifact points to private path: {kind}")
        if artifact["visibility"] == "PRIVATE" and relative[0] not in {"private-evaluator", "qualification"}:
            raise ReproductionError(f"private artifact points outside private path: {kind}")

    seen: dict[str, str] = {}
    for partition, content in document["partitions"].items():
        if content["status"] == "NOT_STARTED" and (content["session_ids"] or content["artifact_sha256s"]):
            raise ReproductionError(f"NOT_STARTED partition contains data: {partition}")
        if content["status"] == "SEALED" and (not content["session_ids"] or not content["artifact_sha256s"]):
            raise ReproductionError(f"SEALED partition lacks session or artifact identity: {partition}")
        for artifact_digest in content["artifact_sha256s"]:
            if artifact_digest in seen and seen[artifact_digest] != partition:
                raise ReproductionError(f"artifact reused across partitions: {artifact_digest}")
            seen[artifact_digest] = partition
    referenced = {
        artifact["sha256"] for artifact in document["artifacts"]
        if artifact["status"] != "MISSING"
    }
    unknown = sorted(set(seen) - referenced)
    if unknown:
        raise ReproductionError(f"partition registry contains unknown artifact: {unknown[0]}")

    verify_readiness(document, by_key, document["lifecycle"])
    if require_lifecycle is not None:
        verify_readiness(document, by_key, require_lifecycle)
    return {
        "task_id": document["task_id"],
        "lifecycle": document["lifecycle"],
        "required_lifecycle": require_lifecycle or document["lifecycle"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CausalPerf task reproduction manifests")
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--schema", type=Path, default=Path(__file__).parents[1] / "schemas" / "task-reproduction-package.schema.json")
    parser.add_argument("--require-lifecycle", choices=LIFECYCLES)
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    try:
        results = [
            validate_manifest(path.resolve(), schema, require_lifecycle=args.require_lifecycle)
            for path in args.tasks
        ]
    except (OSError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as error:
        print(f"FAIL {error}")
        return 1
    print("PASS " + ", ".join(f"{item['task_id']}={item['lifecycle']}" for item in results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
