#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import jsonschema
import yaml


SCHEMA = Path(__file__).parents[1] / "schemas" / "hidden-correctness-suite.schema.json"


class HiddenCorrectnessError(ValueError):
    pass


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def canonical_digest(document: dict) -> str:
    value = {key: item for key, item in document.items() if key != "content_sha256"}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def contained(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise HiddenCorrectnessError(f"unsafe {label} path: {relative}")
    physical_root = root.resolve()
    current = physical_root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise HiddenCorrectnessError(f"{label} path contains symlink: {relative}")
    resolved = (physical_root / candidate).resolve()
    try:
        resolved.relative_to(physical_root)
    except ValueError as error:
        raise HiddenCorrectnessError(f"{label} path escapes root: {relative}") from error
    return resolved


def validate(hidden_root: Path, public_root: Path) -> dict:
    hidden_root = hidden_root.resolve()
    public_root = public_root.resolve()
    for root, label in ((hidden_root, "hidden root"), (public_root, "public root")):
        if not root.is_dir() or root.is_symlink():
            raise HiddenCorrectnessError(f"{label} is not a real directory")
        symlink = next((path for path in root.rglob("*") if path.is_symlink()), None)
        if symlink is not None:
            raise HiddenCorrectnessError(f"{label} contains symlink: {symlink}")
    document = json.loads((hidden_root / "suite.json").read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(document)
    if canonical_digest(document) != document["content_sha256"]:
        raise HiddenCorrectnessError("hidden suite content_sha256 mismatch")

    task = yaml.safe_load((public_root / "task.yaml").read_text(encoding="utf-8"))
    if document["task_id"] != task["id"] or document["task_version"] != task["version"]:
        raise HiddenCorrectnessError("hidden suite and public task identities differ")
    if document["command"] != task["commands"]["correctness"]:
        raise HiddenCorrectnessError("hidden suite does not use the sealed public correctness command")

    destinations: set[str] = set()
    public_names = {path.name for path in public_root.rglob("*") if path.is_file()}
    for entry in document["files"]:
        source = contained(hidden_root, entry["source"], "hidden source")
        destination = contained(public_root, entry["destination"], "overlay destination")
        if not source.is_file() or source.is_symlink():
            raise HiddenCorrectnessError(f"hidden source is not a regular file: {entry['source']}")
        if sha256(source) != entry["sha256"]:
            raise HiddenCorrectnessError(f"hidden source digest mismatch: {entry['source']}")
        if entry["destination"] in destinations:
            raise HiddenCorrectnessError(f"duplicate overlay destination: {entry['destination']}")
        destinations.add(entry["destination"])
        if destination.exists():
            raise HiddenCorrectnessError(f"hidden overlay would replace a public file: {entry['destination']}")
        if source.name in public_names:
            raise HiddenCorrectnessError(f"hidden test filename leaked into public package: {source.name}")

    main_root = public_root / "app" / "src" / "main"
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(main_root.rglob("*"))
        if path.is_file()
    ).casefold()
    findings = [
        token for token in document["forbidden_main_source_tokens"]
        if token.casefold() in source_text
    ]
    if findings:
        raise HiddenCorrectnessError(
            "application main source contains evaluator/benchmark detection tokens: "
            + ", ".join(sorted(findings))
        )
    return {
        "id": document["id"],
        "suite_id": document["suite_id"],
        "files": len(document["files"]),
        "content_sha256": document["content_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an evaluator-only hidden correctness suite")
    parser.add_argument("hidden_root", type=Path)
    parser.add_argument("public_root", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.hidden_root, args.public_root)
    except (OSError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as error:
        print(f"FAIL {error}")
        return 1
    print(f"PASS {result['suite_id']} {result['files']} overlay file(s) {result['content_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
