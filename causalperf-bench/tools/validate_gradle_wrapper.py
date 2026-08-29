#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tomllib
from pathlib import Path

import yaml


class GradleToolchainError(ValueError):
    pass


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_properties(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in result:
            raise GradleToolchainError(f"invalid Wrapper property: {raw_line}")
        result[key] = value.replace(r"\:", ":")
    return result


def require_text(path: Path, patterns: tuple[str, ...]) -> None:
    value = path.read_text(encoding="utf-8")
    missing = [pattern for pattern in patterns if pattern not in value]
    if missing:
        raise GradleToolchainError(f"{path.name} is missing declarations: {missing}")


def validate(task_root: Path) -> dict:
    root = task_root.resolve()
    lock_path = root / "toolchain.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GradleToolchainError(f"cannot load toolchain lock: {error}") from error
    if set(lock) != {"schema_version", "task_id", "gradle", "java", "android", "dependencies"}:
        raise GradleToolchainError("toolchain lock has unknown or missing top-level fields")
    if lock["schema_version"] != 1:
        raise GradleToolchainError("unsupported toolchain lock schema version")

    wrapper = root / "gradle" / "wrapper"
    files = {
        "wrapper_jar_sha256": wrapper / "gradle-wrapper.jar",
        "posix_script_sha256": root / "gradlew",
        "windows_script_sha256": root / "gradlew.bat",
    }
    for field, path in files.items():
        if not path.is_file():
            raise GradleToolchainError(f"missing Gradle Wrapper file: {path.relative_to(root)}")
        actual = sha256(path)
        if actual != lock["gradle"][field]:
            raise GradleToolchainError(
                f"{path.name} SHA-256 mismatch: expected {lock['gradle'][field]}, got {actual}"
            )
    if os.name != "nt" and not (root / "gradlew").stat().st_mode & stat.S_IXUSR:
        raise GradleToolchainError("gradlew is not executable")

    properties = load_properties(wrapper / "gradle-wrapper.properties")
    expected_properties = {
        "distributionUrl": lock["gradle"]["distribution_url"],
        "distributionSha256Sum": lock["gradle"]["distribution_sha256"],
        "validateDistributionUrl": "true",
    }
    for key, expected in expected_properties.items():
        if properties.get(key) != expected:
            raise GradleToolchainError(f"Wrapper property {key} is not locked to {expected}")
    version = lock["gradle"]["version"]
    if f"gradle-{version}-bin.zip" not in properties["distributionUrl"]:
        raise GradleToolchainError("Wrapper URL and locked Gradle version differ")

    catalog_path = root / "gradle" / "libs.versions.toml"
    try:
        catalog = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise GradleToolchainError(f"cannot load version catalog: {error}") from error
    versions = catalog.get("versions", {})
    expected_versions = {"agp": lock["android"]["agp"], **lock["dependencies"]}
    if versions != expected_versions:
        raise GradleToolchainError(
            f"version catalog differs from toolchain lock: expected {expected_versions}, got {versions}"
        )

    require_text(
        root / "build.gradle.kts",
        (
            "alias(libs.plugins.android.application)",
            "alias(libs.plugins.android.test)",
            "dependencyLocking",
            "lockAllConfigurations()",
        ),
    )
    android_tokens = (
        f"compileSdk = {lock['android']['compile_sdk']}",
        f'buildToolsVersion = "{lock["android"]["build_tools"]}"',
        f"targetSdk = {lock['android']['target_sdk']}",
        f"JavaVersion.VERSION_{lock['java']['source_compatibility']}",
    )
    require_text(root / "app" / "build.gradle.kts", android_tokens)
    require_text(root / "macrobenchmark" / "build.gradle.kts", android_tokens)

    task = yaml.safe_load((root / "task.yaml").read_text(encoding="utf-8"))
    if not isinstance(task, dict) or task.get("id") != lock["task_id"]:
        raise GradleToolchainError("task and toolchain lock identities differ")
    for name, command in task["commands"].items():
        if name in {"build", "correctness", "performance"} and command["executable"] != "gradle-wrapper":
            raise GradleToolchainError(f"{name} does not use the pinned Gradle Wrapper")
    if task["commands"]["build"]["args"][:1] != ["clean"]:
        raise GradleToolchainError("build command is not a clean build")

    return {
        "task_id": lock["task_id"],
        "gradle": version,
        "agp": lock["android"]["agp"],
        "wrapper_jar_sha256": lock["gradle"]["wrapper_jar_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a pinned Android Gradle toolchain")
    parser.add_argument("task_root", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.task_root)
    except (GradleToolchainError, OSError, KeyError, TypeError) as error:
        print(f"FAIL {error}")
        return 1
    print(
        f"PASS {result['task_id']} Gradle {result['gradle']} "
        f"AGP {result['agp']} Wrapper {result['wrapper_jar_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
