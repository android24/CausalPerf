#!/usr/bin/env python3
"""Validate a CausalPerf public task and optional private evaluator package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml


BENCH_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = BENCH_ROOT / "schemas"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected an object in {path}")
    return loaded


def validate(instance: dict[str, Any], schema_name: str) -> None:
    schema = load_json(SCHEMA_ROOT / schema_name)
    jsonschema.Draft202012Validator(schema).validate(instance)


def normalize_relative(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Unsafe task-relative path: {path}")
    return candidate


def assert_access_boundaries(task: dict[str, Any]) -> None:
    access = task["agent_access"]
    writable = [normalize_relative(item) for item in access["writable_paths"]]
    protected = [normalize_relative(item) for item in access["protected_paths"]]
    for writable_path in writable:
        for protected_path in protected:
            if (
                writable_path == protected_path
                or writable_path in protected_path.parents
                or protected_path in writable_path.parents
            ):
                raise ValueError(
                    f"Writable/protected paths overlap: {writable_path} and {protected_path}"
                )


def assert_public_package(public_dir: Path) -> None:
    forbidden_names = {
        ".git", "private-evaluator", "ground-truth.json", "expert-patch.diff",
        "evaluator-policy.json", "evaluation-canaries.json", "hidden-tests",
    }
    leaked = [path for path in public_dir.rglob("*") if path.name in forbidden_names]
    if leaked:
        raise ValueError(f"Private evaluator material leaked into public task: {leaked}")
    for path in public_dir.rglob("*"):
        if path.is_symlink():
            resolved = path.resolve()
            if public_dir not in resolved.parents and resolved != public_dir:
                raise ValueError(f"Symlink escapes public task: {path} -> {resolved}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_private(private_dir: Path, public_task: dict[str, Any]) -> None:
    ground_truth = load_json(private_dir / "ground-truth.json")
    validate(ground_truth, "private-ground-truth.schema.json")
    if ground_truth["task_id"] != public_task["id"]:
        raise ValueError("Public task and private Ground Truth task IDs differ")
    if ground_truth["task_version"] != public_task["version"]:
        raise ValueError("Public task and private Ground Truth versions differ")
    canaries = load_json(private_dir / "evaluation-canaries.json")
    validate(canaries, "private-canary-set.schema.json")
    if canaries["task_id"] != public_task["id"] or canaries["task_version"] != public_task["version"]:
        raise ValueError("Public task and private canary identities differ")
    expected_canary_digest = canaries["content_sha256"]
    canonical = {
        key: value for key, value in canaries.items() if key != "content_sha256"
    }
    actual_canary_digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected_canary_digest != actual_canary_digest:
        raise ValueError("Private canary set digest mismatch")
    artifact = ground_truth["reference_patch"]["artifact"]
    patch_path = private_dir / normalize_relative(artifact["path"])
    actual = sha256(patch_path)
    if actual != artifact["sha256"]:
        raise ValueError(
            f"Reference patch digest mismatch: expected {artifact['sha256']}, got {actual}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("public_task", type=Path)
    parser.add_argument("--private-evaluator", type=Path)
    args = parser.parse_args()

    public_dir = args.public_task.resolve()
    public_task = load_yaml(public_dir / "task.yaml")
    validate(public_task, "public-task.schema.json")
    assert_access_boundaries(public_task)
    assert_public_package(public_dir)
    if args.private_evaluator:
        validate_private(args.private_evaluator.resolve(), public_task)

    print(f"PASS {public_task['id']}@{public_task['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
