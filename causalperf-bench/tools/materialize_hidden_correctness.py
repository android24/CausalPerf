#!/usr/bin/env python3
"""Create an evaluator-only task workspace with a sealed hidden-test overlay."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import uuid
from pathlib import Path


_VALIDATOR_PATH = Path(__file__).with_name("validate_hidden_correctness.py")
_SPEC = importlib.util.spec_from_file_location(
    "causalperf_validate_hidden_correctness", _VALIDATOR_PATH
)
assert _SPEC and _SPEC.loader
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)


class HiddenMaterializationError(ValueError):
    pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_disjoint(left: Path, right: Path, label: str) -> None:
    if _is_within(left, right) or _is_within(right, left):
        raise HiddenMaterializationError(f"{label} must be physically disjoint")


def _reject_symlinks(root: Path, label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        raise HiddenMaterializationError(f"{label} is not a real directory: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise HiddenMaterializationError(f"{label} contains symlink: {path}")


def _tree_digest(root: Path) -> str:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise HiddenMaterializationError(f"workspace contains symlink: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _VALIDATOR.sha256(path),
                }
            )
    encoded = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materialize(hidden_root: Path, public_root: Path, destination: Path) -> dict:
    """Copy the public task and overlay hidden files without mutating either input."""
    hidden_root = hidden_root.resolve()
    public_root = public_root.resolve()
    # Resolve existing symlinked prefixes (notably /var -> /private/var on macOS)
    # while still allowing the final destination not to exist yet.
    destination = destination.resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise HiddenMaterializationError(f"destination already exists: {destination}")
    _require_disjoint(public_root, hidden_root, "public and hidden roots")
    _require_disjoint(destination, public_root, "destination and public root")
    _require_disjoint(destination, hidden_root, "destination and hidden root")
    _reject_symlinks(public_root, "public root")
    _reject_symlinks(hidden_root, "hidden root")

    validated = _VALIDATOR.validate(hidden_root, public_root)
    document = json.loads((hidden_root / "suite.json").read_text(encoding="utf-8"))
    public_digest_before = _tree_digest(public_root)
    hidden_digest_before = _tree_digest(hidden_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.stage-{uuid.uuid4().hex}"
    try:
        shutil.copytree(public_root, stage, copy_function=shutil.copy2)
        for entry in document["files"]:
            source = _VALIDATOR.contained(hidden_root, entry["source"], "hidden source")
            target = _VALIDATOR.contained(stage, entry["destination"], "overlay destination")
            if target.exists() or target.is_symlink():
                raise HiddenMaterializationError(
                    f"overlay destination already exists: {entry['destination']}"
                )
            if _VALIDATOR.sha256(source) != entry["sha256"]:
                raise HiddenMaterializationError(
                    f"hidden source changed before copy: {entry['source']}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if _VALIDATOR.sha256(target) != entry["sha256"]:
                raise HiddenMaterializationError(
                    f"overlay digest mismatch after copy: {entry['destination']}"
                )

        if _tree_digest(public_root) != public_digest_before:
            raise HiddenMaterializationError("public task changed during materialization")
        if _tree_digest(hidden_root) != hidden_digest_before:
            raise HiddenMaterializationError("hidden suite changed during materialization")
        if destination.exists() or destination.is_symlink():
            raise HiddenMaterializationError(f"destination appeared during copy: {destination}")
        workspace_digest = _tree_digest(stage)
        stage.rename(destination)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    return {
        "suite_id": validated["suite_id"],
        "suite_sha256": validated["content_sha256"],
        "public_task_sha256": public_digest_before,
        "hidden_package_sha256": hidden_digest_before,
        "workspace_sha256": workspace_digest,
        "overlay_file_count": validated["files"],
        "destination": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a private correctness workspace for the evaluator"
    )
    parser.add_argument("hidden_root", type=Path)
    parser.add_argument("public_root", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        result = materialize(args.hidden_root, args.public_root, args.destination)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
