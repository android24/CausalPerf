#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath


FORBIDDEN_NAMES = {".git", ".gradle", "private-evaluator", "ground-truth.json", "expert-patch.diff", "hidden-tests", "evaluator-policy"}
FORBIDDEN_TEXT = ("CAUSALPERF_PRIVATE_CANARY_", "BEGIN PRIVATE GROUND TRUTH")


class ExportError(ValueError):
    pass


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect(source: Path) -> list[Path]:
    if not source.is_dir() or source.is_symlink():
        raise ExportError("source must be a real directory")
    files: list[Path] = []
    for root, directories, names in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in directories + names:
            path = root_path / name
            relative = path.relative_to(source)
            if any(part in FORBIDDEN_NAMES for part in relative.parts):
                raise ExportError(f"forbidden path: {relative}")
            if path.is_symlink():
                raise ExportError(f"symlink forbidden: {relative}")
        for name in names:
            path = root_path / name
            data = path.read_bytes()
            if any(marker.encode() in data for marker in FORBIDDEN_TEXT):
                raise ExportError(f"private canary or marker found: {path.relative_to(source)}")
            files.append(path)
    return sorted(files)


def export(source: Path, destination: Path) -> dict:
    files = inspect(source)
    if destination.exists():
        raise ExportError("destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="causalperf-export-", dir=destination.parent))
    try:
        manifest_files = []
        for path in files:
            relative = path.relative_to(source)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            manifest_files.append({"path": PurePosixPath(relative).as_posix(), "sha256": sha256(target), "size": target.stat().st_size})
        manifest = {"schema_version": 1, "files": manifest_files}
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest["content_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_path = stage / "public-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        stage.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a clean, read-only CausalPerf public task package")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = export(args.source.resolve(), args.destination.resolve())
    print(f"PASS {len(manifest['files'])} files {manifest['content_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
