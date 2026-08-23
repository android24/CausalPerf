#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote


REQUIRED_PATHS = (
    "README.md", "docs", "shared/docs", "shared/schemas", "shared/reference",
    "causalperf-agent/docs", "causalperf-agent/schemas", "causalperf-agent/tests",
    "causalperf-bench/docs",
    "causalperf-bench/schemas", "causalperf-bench/tasks",
    "causalperf-bench/tools", "causalperf-bench/tests",
)
PRIVATE_NAMES = {
    "ground-truth.json", "expert-patch.diff", "hidden-tests",
    "private-evaluator", "evaluator-policy.json", "evaluation-canaries.json",
}
IGNORED_LINK_PREFIXES = ("http://", "https://", "mailto:", "chatgpt-", "#")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


class RepositoryError(ValueError):
    pass


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True,
        stdout=subprocess.PIPE,
    )
    return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def _repository_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def validate_layout(root: Path) -> None:
    missing = [item for item in REQUIRED_PATHS if not (root / item).exists()]
    if missing:
        raise RepositoryError(f"missing required paths: {', '.join(missing)}")


def validate_tracked_generated_files(root: Path, tracked: list[Path]) -> None:
    bad = []
    for path in tracked:
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store":
            bad.append(relative.as_posix())
    if bad:
        raise RepositoryError(f"generated files are tracked: {', '.join(sorted(bad))}")


def validate_ownership(root: Path, tracked: list[Path]) -> None:
    bad = []
    for path in tracked:
        relative = path.relative_to(root)
        if not relative.parts:
            continue
        if relative.parts[0] in {"shared", "causalperf-agent"} and any(part in PRIVATE_NAMES for part in relative.parts):
            bad.append(relative.as_posix())
        if "private-evaluator" in relative.parts and relative.parts[0] != "causalperf-bench":
            bad.append(relative.as_posix())
    if bad:
        raise RepositoryError(f"ownership boundary violation: {', '.join(sorted(set(bad)))}")


def validate_public_tasks(root: Path) -> None:
    task_root = root / "causalperf-bench" / "tasks"
    bad = []
    for public in task_root.glob("**/public-task"):
        for current, directories, files in os.walk(public, followlinks=False):
            for name in directories + files:
                path = Path(current) / name
                relative = path.relative_to(root)
                if name in PRIVATE_NAMES or path.is_symlink():
                    bad.append(relative.as_posix())
    if bad:
        raise RepositoryError(f"unsafe public task content: {', '.join(sorted(bad))}")


def validate_markdown_links(root: Path, tracked: list[Path]) -> None:
    broken = []
    for document in tracked:
        if document.suffix.lower() != ".md" or not document.is_file():
            continue
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(IGNORED_LINK_PREFIXES) or target.startswith("/"):
                continue
            resolved = (document.parent / unquote(target)).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                broken.append(f"{document.relative_to(root)} -> {raw_target} (escapes root)")
                continue
            if not resolved.exists():
                broken.append(f"{document.relative_to(root)} -> {raw_target}")
    if broken:
        raise RepositoryError("broken Markdown links: " + "; ".join(broken))


def validate_json_schemas(root: Path) -> int:
    try:
        from jsonschema.validators import validator_for
    except ImportError as error:
        raise RepositoryError("jsonschema dependency is required") from error
    schemas = sorted((root / "shared" / "schemas").glob("*.json"))
    schemas += sorted((root / "causalperf-bench" / "schemas").glob("**/*.json"))
    schemas += sorted((root / "causalperf-agent" / "schemas").glob("**/*.schema.json"))
    for path in schemas:
        value = json.loads(path.read_text(encoding="utf-8"))
        validator_for(value).check_schema(value)
    return len(schemas)


def validate(root: Path) -> dict:
    root = root.resolve()
    validate_layout(root)
    tracked = _tracked_files(root)
    files = _repository_files(root)
    validate_tracked_generated_files(root, tracked)
    validate_ownership(root, files)
    validate_public_tasks(root)
    validate_markdown_links(root, files)
    schema_count = validate_json_schemas(root)
    return {"tracked_files": len(tracked), "repository_files": len(files), "schemas": schema_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CausalPerf repository boundaries")
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    try:
        result = validate(args.root)
    except (RepositoryError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"FAIL {error}")
        return 1
    print(f"PASS {result['repository_files']} repository files "
          f"({result['tracked_files']} tracked), {result['schemas']} schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
