#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import jsonschema


IMPLEMENTED = {"TASK_SPEC", "SOURCE", "PUBLIC_TASK", "CORRECTNESS_TESTS", "BENCHMARK_SCENARIO", "GROUND_TRUTH", "REFERENCE_PATCH"}
CALIBRATED = IMPLEMENTED | {"ENVIRONMENT_SNAPSHOT", "A1_B_A2_MEASUREMENTS", "TRACES", "MECHANISM_EVIDENCE", "VARIANCE_REPORT"}
QUALIFIED = CALIBRATED | {"INDEPENDENT_REPLAY", "LEAKAGE_REVIEW"}
REQUIRED = {"DRAFT": {"TASK_SPEC"}, "IMPLEMENTED": IMPLEMENTED, "CALIBRATED": CALIBRATED, "QUALIFIED": QUALIFIED, "FROZEN": QUALIFIED}


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


def validate_manifest(task_dir: Path, schema: dict) -> dict:
    manifest_path = task_dir / "reproduction.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema, format_checker=jsonschema.FormatChecker())
    by_kind: dict[str, dict] = {}
    for artifact in document["artifacts"]:
        kind = artifact["kind"]
        if kind in by_kind:
            raise ReproductionError(f"duplicate artifact kind: {kind}")
        by_kind[kind] = artifact
        if artifact["status"] == "MISSING":
            continue
        target = (task_dir / artifact["relative_path"]).resolve()
        try:
            target.relative_to(task_dir.resolve())
        except ValueError as error:
            raise ReproductionError(f"artifact escapes task: {kind}") from error
        if digest_path(target) != artifact["sha256"]:
            raise ReproductionError(f"artifact digest mismatch: {kind}")
        relative = target.relative_to(task_dir.resolve()).parts
        if artifact["visibility"] == "PUBLIC" and relative[0] == "private-evaluator":
            raise ReproductionError(f"public artifact points to private path: {kind}")
        if artifact["visibility"] == "PRIVATE" and relative[0] not in {"private-evaluator", "qualification"}:
            raise ReproductionError(f"private artifact points outside private path: {kind}")

    required = REQUIRED[document["lifecycle"]]
    missing = sorted(kind for kind in required if kind not in by_kind or by_kind[kind]["status"] == "MISSING")
    if missing:
        raise ReproductionError(f"{document['lifecycle']} package incomplete: {', '.join(missing)}")
    if document["lifecycle"] in {"CALIBRATED", "QUALIFIED", "FROZEN"}:
        unverified = sorted(kind for kind in required if by_kind[kind]["status"] != "VERIFIED")
        if unverified:
            raise ReproductionError(f"{document['lifecycle']} artifacts not verified: {', '.join(unverified)}")
    if document["lifecycle"] in {"QUALIFIED", "FROZEN"}:
        for partition in ("CALIBRATION", "QUALIFICATION"):
            if document["partitions"][partition]["status"] != "SEALED":
                raise ReproductionError(f"{partition} partition must be SEALED")

    seen: dict[str, str] = {}
    for partition, content in document["partitions"].items():
        for artifact_digest in content["artifact_sha256s"]:
            if artifact_digest in seen and seen[artifact_digest] != partition:
                raise ReproductionError(f"artifact reused across partitions: {artifact_digest}")
            seen[artifact_digest] = partition
    return {"task_id": document["task_id"], "lifecycle": document["lifecycle"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CausalPerf task reproduction manifests")
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--schema", type=Path, default=Path(__file__).parents[1] / "schemas" / "task-reproduction-package.schema.json")
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    try:
        results = [validate_manifest(path.resolve(), schema) for path in args.tasks]
    except (OSError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as error:
        print(f"FAIL {error}")
        return 1
    print("PASS " + ", ".join(f"{item['task_id']}={item['lifecycle']}" for item in results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
