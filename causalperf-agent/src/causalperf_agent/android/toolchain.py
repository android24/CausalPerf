from __future__ import annotations

import os
import platform
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

from causalperf_reference.artifacts import digest


SUPPORTED_HOSTS = {"Darwin", "Windows", "Linux"}
PROFILE_FIELDS = {
    "host_os",
    "jdk_home",
    "android_sdk_root",
    "gradle_home",
    "java_executable",
    "adb_executable",
    "gradle_executable",
}


class ToolchainConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ToolchainProfile:
    name: str
    host_os: str
    jdk_home: str | None = None
    android_sdk_root: str | None = None
    gradle_home: str | None = None
    java_executable: str | None = None
    adb_executable: str | None = None
    gradle_executable: str | None = None


@dataclass(frozen=True)
class ResolvedToolchain:
    profile_name: str
    host_os: str
    tool_paths: Mapping[str, str]
    sdk_root: str | None
    environment: Mapping[str, str]
    configuration_sha256: str


def load_toolchain_profile(
    path: str | Path,
    *,
    profile_name: str | None = None,
    host_os: str | None = None,
) -> ToolchainProfile:
    current_host = host_os or platform.system()
    if current_host not in SUPPORTED_HOSTS:
        raise ToolchainConfigError(f"unsupported host OS: {current_host}")
    try:
        with Path(path).open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ToolchainConfigError(f"cannot load toolchain config: {error}") from error
    if set(document) != {"schema_version", "profiles"}:
        raise ToolchainConfigError("toolchain config must contain only schema_version and profiles")
    if document["schema_version"] != 1:
        raise ToolchainConfigError("unsupported toolchain config schema version")
    profiles = document["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ToolchainConfigError("toolchain config profiles must be a non-empty table")

    if profile_name is None:
        candidates = [
            name
            for name, value in profiles.items()
            if isinstance(value, dict) and value.get("host_os") == current_host
        ]
        if len(candidates) != 1:
            raise ToolchainConfigError(
                f"expected exactly one profile for {current_host}, found {len(candidates)}"
            )
        profile_name = candidates[0]
    if profile_name not in profiles or not isinstance(profiles[profile_name], dict):
        raise ToolchainConfigError(f"unknown toolchain profile: {profile_name}")

    value = profiles[profile_name]
    unknown = set(value) - PROFILE_FIELDS
    if unknown:
        raise ToolchainConfigError(f"unknown fields in profile {profile_name}: {sorted(unknown)}")
    missing = {"host_os"} - set(value)
    if missing:
        raise ToolchainConfigError(f"profile {profile_name} is missing host_os")
    if value["host_os"] != current_host:
        raise ToolchainConfigError(
            f"profile {profile_name} targets {value['host_os']}, current host is {current_host}"
        )
    for key, item in value.items():
        if not isinstance(item, str) or not item.strip():
            raise ToolchainConfigError(f"profile {profile_name}.{key} must be a non-empty string")
    for key in ("jdk_home", "android_sdk_root", "gradle_home"):
        item = value.get(key)
        if item is not None and not _is_absolute(item, current_host):
            raise ToolchainConfigError(f"profile {profile_name}.{key} must be an absolute path")
    return ToolchainProfile(name=profile_name, **value)


def resolve_toolchain(
    profile: ToolchainProfile | None = None,
    *,
    overrides: Mapping[str, str | None] | None = None,
    environment: Mapping[str, str] | None = None,
    host_os: str | None = None,
) -> ResolvedToolchain:
    current_host = host_os or platform.system()
    if current_host not in SUPPORTED_HOSTS:
        raise ToolchainConfigError(f"unsupported host OS: {current_host}")
    if profile is not None and profile.host_os != current_host:
        raise ToolchainConfigError("toolchain profile host does not match current host")
    supplied = dict(overrides or {})
    unknown = set(supplied) - (PROFILE_FIELDS - {"host_os"})
    if unknown:
        raise ToolchainConfigError(f"unknown toolchain overrides: {sorted(unknown)}")
    source_environment = dict(os.environ if environment is None else environment)

    def selected_root(field: str, environment_key: str) -> str | None:
        return supplied.get(field) or getattr(profile, field, None) or source_environment.get(environment_key)

    jdk_home = selected_root("jdk_home", "JAVA_HOME")
    sdk_root = (
        supplied.get("android_sdk_root")
        or getattr(profile, "android_sdk_root", None)
        or source_environment.get("ANDROID_SDK_ROOT")
        or source_environment.get("ANDROID_HOME")
    )
    gradle_home = selected_root("gradle_home", "GRADLE_HOME")

    def executable(name: str, home: str | None, *relative: str) -> str | None:
        override = supplied.get(f"{name}_executable")
        if override:
            return override
        root_override = supplied.get({"java": "jdk_home", "adb": "android_sdk_root", "gradle": "gradle_home"}[name])
        if root_override and home:
            return _join(home, current_host, *relative)
        configured = getattr(profile, f"{name}_executable", None) if profile else None
        if configured:
            return configured
        return _join(home, current_host, *relative) if home else None

    java_name = "java.exe" if current_host == "Windows" else "java"
    adb_name = "adb.exe" if current_host == "Windows" else "adb"
    gradle_name = "gradle.bat" if current_host == "Windows" else "gradle"
    candidates = {
        "java": executable("java", jdk_home, "bin", java_name),
        "adb": executable("adb", sdk_root, "platform-tools", adb_name),
        "gradle": executable("gradle", gradle_home, "bin", gradle_name),
    }
    tool_paths = {name: value for name, value in candidates.items() if value is not None}

    child_environment = dict(source_environment)
    if jdk_home:
        child_environment["JAVA_HOME"] = jdk_home
    if sdk_root:
        child_environment["ANDROID_SDK_ROOT"] = sdk_root
        child_environment["ANDROID_HOME"] = sdk_root
    if gradle_home:
        child_environment["GRADLE_HOME"] = gradle_home

    sealed_view = {
        "profile_name": profile.name if profile else "ambient",
        "host_os": current_host,
        "tool_paths": tool_paths,
        "sdk_root": sdk_root,
        "jdk_home": jdk_home,
        "gradle_home": gradle_home,
    }
    return ResolvedToolchain(
        profile_name=sealed_view["profile_name"],
        host_os=current_host,
        tool_paths=tool_paths,
        sdk_root=sdk_root,
        environment=child_environment,
        configuration_sha256=digest(sealed_view),
    )


def _join(root: str, host_os: str, *relative: str) -> str:
    path_type = PureWindowsPath if host_os == "Windows" else PurePosixPath
    return str(path_type(root).joinpath(*relative))


def _is_absolute(value: str, host_os: str) -> bool:
    path_type = PureWindowsPath if host_os == "Windows" else PurePosixPath
    return path_type(value).is_absolute()
