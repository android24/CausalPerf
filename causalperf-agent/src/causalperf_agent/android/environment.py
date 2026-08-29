from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from causalperf_reference.artifacts import digest

from .toolchain import ToolchainConfigError, load_toolchain_profile, resolve_toolchain


THERMAL_STATUS = {
    0: "NONE",
    1: "LIGHT",
    2: "MODERATE",
    3: "SEVERE",
    4: "CRITICAL",
    5: "EMERGENCY",
    6: "SHUTDOWN",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CommandOutput:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], timeout_seconds: int) -> CommandOutput: ...


class SubprocessCommandRunner:
    """Run an exact argument vector without a host shell."""

    _ENVIRONMENT_KEYS = (
        "PATH",
        "HOME",
        "JAVA_HOME",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "GRADLE_HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "ComSpec",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "PATHEXT",
    )

    def __init__(self, environment: Mapping[str, str] | None = None):
        source = os.environ if environment is None else environment
        self.environment = {key: source[key] for key in self._ENVIRONMENT_KEYS if key in source}

    def run(self, argv: Sequence[str], timeout_seconds: int) -> CommandOutput:
        try:
            result = subprocess.run(
                list(argv),
                check=False,
                cwd=None,
                env=self.environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"command transport failed: {argv[0]}") from error
        return CommandOutput(result.returncode, result.stdout, result.stderr)


@dataclass(frozen=True)
class AndroidLabRequirements:
    min_api: int = 34
    max_api: int = 36
    allowed_abis: tuple[str, ...] = ("arm64-v8a",)
    physical_device_required: bool = True
    minimum_java_major: int = 17
    required_gradle_version: str = "9.5.0"
    sdk_platform: int = 36
    build_tools_version: str = "36.0.0"
    min_battery_percent: float = 50.0
    charging: str = "ANY"
    allowed_thermal_statuses: tuple[str, ...] = ("NONE", "LIGHT")
    expected_online_cpu_count: int | None = None
    min_available_memory_mb: float = 2048.0
    max_background_load_percent: float = 20.0
    compilation_mode: str = "none"

    def __post_init__(self) -> None:
        if self.min_api > self.max_api:
            raise ValueError("minimum API cannot exceed maximum API")
        if not self.allowed_abis:
            raise ValueError("at least one ABI is required")
        if self.charging not in {"ANY", "REQUIRED", "FORBIDDEN"}:
            raise ValueError("charging must be ANY, REQUIRED, or FORBIDDEN")


@dataclass(frozen=True)
class PreflightResult:
    status: str
    reason_codes: tuple[str, ...]
    environment_snapshot: dict | None = None

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "INCONCLUSIVE"}:
            raise ValueError(f"invalid preflight status: {self.status}")

    def to_dict(self) -> dict:
        value = {"status": self.status, "reason_codes": list(self.reason_codes)}
        if self.environment_snapshot is not None:
            value["environment_snapshot"] = self.environment_snapshot
        return value


class ProbeFailure(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class AndroidEnvironmentCollector:
    """Fail-closed, read-only Android laboratory preflight.

    The collector never auto-selects a device, starts a benchmark, installs an
    APK, or mutates device state. A passing result is an EnvironmentSnapshot;
    a host or transport deficiency remains an INCONCLUSIVE operational result.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        path_exists: Callable[[Path], bool] = Path.exists,
        environment: Mapping[str, str] | None = None,
        tool_paths: Mapping[str, str] | None = None,
        sdk_root: str | Path | None = None,
        toolchain_metadata: Mapping[str, str] | None = None,
        clock: Callable[[], str] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        load_sample_seconds: float = 0.25,
    ):
        self.environment = dict(os.environ if environment is None else environment)
        self.runner = runner or SubprocessCommandRunner(self.environment)
        self.which = which
        self.path_exists = path_exists
        self.tool_paths = dict(tool_paths or {})
        unknown_tools = set(self.tool_paths) - {"java", "adb", "gradle"}
        if unknown_tools:
            raise ValueError(f"unknown Android lab tools: {sorted(unknown_tools)}")
        self.sdk_root = Path(sdk_root) if sdk_root is not None else None
        self.toolchain_metadata = dict(toolchain_metadata or {})
        if any(not isinstance(key, str) or not isinstance(value, str) or not value
               for key, value in self.toolchain_metadata.items()):
            raise ValueError("toolchain metadata must contain non-empty strings")
        self.clock = clock
        self.sleep = sleep
        self.load_sample_seconds = load_sample_seconds

    def collect(
        self,
        *,
        device_serial: str,
        environment_id: str,
        requirements: AndroidLabRequirements | None = None,
    ) -> PreflightResult:
        requirements = requirements or AndroidLabRequirements()
        if not device_serial or any(character.isspace() for character in device_serial):
            return PreflightResult("INCONCLUSIVE", ("EXPLICIT_DEVICE_SERIAL_REQUIRED",))
        if not re.fullmatch(r"ENV-[A-Z0-9-]+", environment_id):
            raise ValueError("environment_id must match ENV-[A-Z0-9-]+")

        executables, host_reasons = self._resolve_host_tools(requirements)
        # Do not execute a partially resolved toolchain. In particular, invoking
        # a Gradle Wrapper while the SDK is absent may download a distribution
        # even though the laboratory can only return INCONCLUSIVE. Structural
        # host readiness therefore gates every version and device probe.
        if host_reasons:
            return PreflightResult("INCONCLUSIVE", tuple(host_reasons))

        versions: dict[str, str] = {}
        probes = {
            "java": self._java_version,
            "adb": self._adb_version,
            "gradle": self._gradle_version,
        }
        for tool, probe in probes.items():
            executable = executables.get(tool)
            if executable is None:
                continue
            try:
                versions[tool] = probe(executable)
            except ProbeFailure as error:
                host_reasons.append(error.reason_code)

        if "java" in versions:
            try:
                java_major = _java_major(versions["java"])
            except ProbeFailure as error:
                host_reasons.append(error.reason_code)
            else:
                if java_major < requirements.minimum_java_major:
                    host_reasons.append("JAVA_VERSION_UNSUPPORTED")
        if (
            "gradle" in versions
            and versions["gradle"] != requirements.required_gradle_version
        ):
            host_reasons.append("GRADLE_VERSION_MISMATCH")
        if host_reasons:
            return PreflightResult("INCONCLUSIVE", tuple(host_reasons))

        java_version = versions["java"]
        adb_version = versions["adb"]
        gradle_version = versions["gradle"]

        try:
            self._require_online_device(executables["adb"], device_serial)
            device = self._device_facts(executables["adb"], device_serial)
            runtime = self._runtime_facts(
                executables["adb"], device_serial, requirements.compilation_mode
            )
        except ProbeFailure as error:
            return PreflightResult("INCONCLUSIVE", (error.reason_code,))

        validity_reasons = self._validity_reasons(device, runtime, requirements)
        snapshot = {
            "schema_version": 1,
            "id": environment_id,
            "captured_at": self.clock(),
            "device": {
                "serial_hash": digest(device_serial),
                "model": device["model"],
                "abi": device["abi"],
                "api_level": device["api_level"],
                "build_fingerprint_sha256": digest(device["build_fingerprint"]),
            },
            "runtime": runtime,
            "toolchain": {
                "java": java_version,
                "adb": adb_version,
                "gradle": gradle_version,
                "android_sdk_platform": str(requirements.sdk_platform),
                "android_build_tools": requirements.build_tools_version,
                **self.toolchain_metadata,
            },
            "validity": {
                "status": "PASS" if not validity_reasons else "INCONCLUSIVE",
                "reason_codes": validity_reasons,
            },
        }
        snapshot["content_sha256"] = digest(snapshot)
        return PreflightResult(
            snapshot["validity"]["status"], tuple(validity_reasons), snapshot
        )

    def _resolve_host_tools(
        self, requirements: AndroidLabRequirements
    ) -> tuple[dict[str, str], list[str]]:
        resolved: dict[str, str] = {}
        reasons: list[str] = []
        for tool in ("java", "adb", "gradle"):
            if tool in self.tool_paths:
                path = self.tool_paths[tool]
                if not self.path_exists(Path(path)):
                    reasons.append(f"HOST_TOOL_MISSING:{tool.upper()}")
                    continue
            else:
                path = self.which(tool)
            if path is None:
                reasons.append(f"HOST_TOOL_MISSING:{tool.upper()}")
            else:
                resolved[tool] = path

        sdk_root_value = self.sdk_root or self.environment.get("ANDROID_SDK_ROOT") or self.environment.get("ANDROID_HOME")
        if not sdk_root_value:
            reasons.append("ANDROID_SDK_ROOT_MISSING")
        else:
            sdk_root = Path(sdk_root_value)
            if not self.path_exists(sdk_root / "platforms" / f"android-{requirements.sdk_platform}" / "android.jar"):
                reasons.append("ANDROID_PLATFORM_MISSING")
            if not self.path_exists(sdk_root / "build-tools" / requirements.build_tools_version):
                reasons.append("ANDROID_BUILD_TOOLS_MISSING")
        return resolved, reasons

    def _checked(self, argv: Sequence[str], reason_code: str, timeout_seconds: int = 15) -> str:
        try:
            output = self.runner.run(argv, timeout_seconds)
        except RuntimeError as error:
            raise ProbeFailure(reason_code) from error
        if output.returncode != 0:
            raise ProbeFailure(reason_code)
        return output.stdout if output.stdout.strip() else output.stderr

    def _java_version(self, java: str) -> str:
        output = self._checked((java, "-version"), "JAVA_VERSION_PROBE_FAILED")
        match = re.search(r'version\s+"([^"]+)"', output)
        if not match:
            raise ProbeFailure("JAVA_VERSION_UNPARSEABLE")
        return match.group(1)

    def _adb_version(self, adb: str) -> str:
        output = self._checked((adb, "version"), "ADB_VERSION_PROBE_FAILED")
        match = re.search(r"Android Debug Bridge version\s+([^\s]+)", output)
        if not match:
            raise ProbeFailure("ADB_VERSION_UNPARSEABLE")
        return match.group(1)

    def _gradle_version(self, gradle: str) -> str:
        output = self._checked((gradle, "--version"), "GRADLE_VERSION_PROBE_FAILED", 30)
        match = re.search(r"^Gradle\s+([^\s]+)", output, re.MULTILINE)
        if not match:
            raise ProbeFailure("GRADLE_VERSION_UNPARSEABLE")
        return match.group(1)

    def _require_online_device(self, adb: str, serial: str) -> None:
        output = self._checked((adb, "devices"), "ADB_DEVICE_LIST_FAILED")
        states: dict[str, str] = {}
        for line in output.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2:
                states[fields[0]] = fields[1]
        if serial not in states:
            raise ProbeFailure("REQUESTED_DEVICE_NOT_FOUND")
        if states[serial] != "device":
            raise ProbeFailure(f"REQUESTED_DEVICE_NOT_ONLINE:{states[serial].upper()}")

    def _adb_shell(self, adb: str, serial: str, *arguments: str) -> str:
        return self._checked(
            (adb, "-s", serial, "shell", *arguments),
            f"DEVICE_PROBE_FAILED:{arguments[0].upper()}",
        ).strip()

    def _device_facts(self, adb: str, serial: str) -> dict:
        try:
            api_level = int(self._adb_shell(adb, serial, "getprop", "ro.build.version.sdk"))
        except ValueError as error:
            raise ProbeFailure("DEVICE_API_UNPARSEABLE") from error
        return {
            "model": self._required_value(
                self._adb_shell(adb, serial, "getprop", "ro.product.model"),
                "DEVICE_MODEL_MISSING",
            ),
            "abi": self._required_value(
                self._adb_shell(adb, serial, "getprop", "ro.product.cpu.abi"),
                "DEVICE_ABI_MISSING",
            ),
            "api_level": api_level,
            "build_fingerprint": self._required_value(
                self._adb_shell(adb, serial, "getprop", "ro.build.fingerprint"),
                "BUILD_FINGERPRINT_MISSING",
            ),
            "emulator": self._adb_shell(adb, serial, "getprop", "ro.kernel.qemu") == "1",
        }

    @staticmethod
    def _required_value(value: str, reason_code: str) -> str:
        if not value:
            raise ProbeFailure(reason_code)
        return value

    def _runtime_facts(self, adb: str, serial: str, compilation_mode: str) -> dict:
        battery = self._adb_shell(adb, serial, "dumpsys", "battery")
        thermal = self._adb_shell(adb, serial, "dumpsys", "thermalservice")
        online = self._adb_shell(adb, serial, "cat", "/sys/devices/system/cpu/online")
        memory = self._adb_shell(adb, serial, "cat", "/proc/meminfo")
        first_cpu = self._adb_shell(adb, serial, "cat", "/proc/stat")
        self.sleep(self.load_sample_seconds)
        second_cpu = self._adb_shell(adb, serial, "cat", "/proc/stat")
        return {
            "battery_percent": _battery_level(battery),
            "charging": _is_charging(battery),
            "thermal_status": _thermal_status(thermal),
            "online_cpu_count": _online_cpu_count(online),
            "available_memory_mb": _available_memory_mb(memory),
            "background_load_percent": _cpu_load_percent(first_cpu, second_cpu),
            "compilation_mode": compilation_mode,
        }

    @staticmethod
    def _validity_reasons(device: dict, runtime: dict, requirements: AndroidLabRequirements) -> list[str]:
        reasons: list[str] = []
        if not requirements.min_api <= device["api_level"] <= requirements.max_api:
            reasons.append("API_LEVEL_OUT_OF_RANGE")
        if device["abi"] not in requirements.allowed_abis:
            reasons.append("ABI_NOT_ALLOWED")
        if requirements.physical_device_required and device["emulator"]:
            reasons.append("PHYSICAL_DEVICE_REQUIRED")
        if runtime["battery_percent"] < requirements.min_battery_percent:
            reasons.append("BATTERY_BELOW_MINIMUM")
        if requirements.charging == "REQUIRED" and not runtime["charging"]:
            reasons.append("CHARGING_REQUIRED")
        if requirements.charging == "FORBIDDEN" and runtime["charging"]:
            reasons.append("CHARGING_FORBIDDEN")
        if runtime["thermal_status"] not in requirements.allowed_thermal_statuses:
            reasons.append("THERMAL_STATUS_NOT_ALLOWED")
        if (
            requirements.expected_online_cpu_count is not None
            and runtime["online_cpu_count"] != requirements.expected_online_cpu_count
        ):
            reasons.append("ONLINE_CPU_COUNT_MISMATCH")
        if runtime["available_memory_mb"] < requirements.min_available_memory_mb:
            reasons.append("AVAILABLE_MEMORY_BELOW_MINIMUM")
        if runtime["background_load_percent"] > requirements.max_background_load_percent:
            reasons.append("BACKGROUND_LOAD_ABOVE_MAXIMUM")
        return reasons


def _java_major(version: str) -> int:
    first = version.split(".", 1)[0]
    if first == "1":
        fields = version.split(".")
        if len(fields) < 2:
            raise ProbeFailure("JAVA_VERSION_UNPARSEABLE")
        first = fields[1]
    match = re.match(r"(\d+)", first)
    if not match:
        raise ProbeFailure("JAVA_VERSION_UNPARSEABLE")
    return int(match.group(1))


def _battery_level(output: str) -> float:
    match = re.search(r"^\s*level:\s*(\d+(?:\.\d+)?)\s*$", output, re.MULTILINE)
    if not match:
        raise ProbeFailure("BATTERY_LEVEL_UNPARSEABLE")
    return float(match.group(1))


def _is_charging(output: str) -> bool:
    powered = re.findall(
        r"^\s*(?:AC|USB|Wireless|Dock) powered:\s*(true|false)\s*$",
        output,
        re.MULTILINE | re.IGNORECASE,
    )
    if not powered:
        raise ProbeFailure("BATTERY_CHARGING_UNPARSEABLE")
    return any(value.lower() == "true" for value in powered)


def _thermal_status(output: str) -> str:
    match = re.search(r"(?:Current Thermal Status:\s*|mStatus=)(\d+)", output)
    if not match:
        return "UNKNOWN"
    return THERMAL_STATUS.get(int(match.group(1)), "UNKNOWN")


def _online_cpu_count(value: str) -> int:
    count = 0
    try:
        for group in value.strip().split(","):
            if "-" in group:
                start, end = (int(item) for item in group.split("-", 1))
                if end < start:
                    raise ValueError
                count += end - start + 1
            else:
                int(group)
                count += 1
    except ValueError as error:
        raise ProbeFailure("ONLINE_CPU_COUNT_UNPARSEABLE") from error
    if count < 1:
        raise ProbeFailure("ONLINE_CPU_COUNT_UNPARSEABLE")
    return count


def _available_memory_mb(output: str) -> float:
    match = re.search(r"^MemAvailable:\s*(\d+)\s+kB\s*$", output, re.MULTILINE)
    if not match:
        raise ProbeFailure("AVAILABLE_MEMORY_UNPARSEABLE")
    return round(int(match.group(1)) / 1024.0, 3)


def _cpu_totals(output: str) -> tuple[int, int]:
    line = next((item for item in output.splitlines() if item.startswith("cpu ")), None)
    if line is None:
        raise ProbeFailure("CPU_LOAD_UNPARSEABLE")
    try:
        values = [int(item) for item in line.split()[1:]]
    except ValueError as error:
        raise ProbeFailure("CPU_LOAD_UNPARSEABLE") from error
    if len(values) < 5:
        raise ProbeFailure("CPU_LOAD_UNPARSEABLE")
    return sum(values), values[3] + values[4]


def _cpu_load_percent(first: str, second: str) -> float:
    first_total, first_idle = _cpu_totals(first)
    second_total, second_idle = _cpu_totals(second)
    total_delta = second_total - first_total
    idle_delta = second_idle - first_idle
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        raise ProbeFailure("CPU_LOAD_SAMPLE_INVALID")
    return round(100.0 * (total_delta - idle_delta) / total_delta, 3)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the Phase 1B Android calibration laboratory")
    parser.add_argument("--device-serial", required=True)
    parser.add_argument("--environment-id", default="ENV-CPU-001-PREFLIGHT")
    parser.add_argument("--java-executable")
    parser.add_argument("--adb-executable")
    parser.add_argument("--gradle-executable")
    parser.add_argument("--sdk-root")
    parser.add_argument("--jdk-home")
    parser.add_argument("--gradle-home")
    parser.add_argument("--toolchain-config")
    parser.add_argument("--toolchain-profile")
    args = parser.parse_args(argv)
    if args.toolchain_profile and not args.toolchain_config:
        parser.error("--toolchain-profile requires --toolchain-config")
    try:
        profile = (
            load_toolchain_profile(
                args.toolchain_config,
                profile_name=args.toolchain_profile,
            )
            if args.toolchain_config
            else None
        )
        resolved = resolve_toolchain(
            profile,
            overrides={
                "java_executable": args.java_executable,
                "adb_executable": args.adb_executable,
                "gradle_executable": args.gradle_executable,
                "jdk_home": args.jdk_home,
                "android_sdk_root": args.sdk_root,
                "gradle_home": args.gradle_home,
            },
        )
    except ToolchainConfigError as error:
        parser.error(str(error))
    result = AndroidEnvironmentCollector(
        environment=resolved.environment,
        tool_paths=resolved.tool_paths,
        sdk_root=resolved.sdk_root,
        toolchain_metadata={
            "host_os": resolved.host_os,
            "profile": resolved.profile_name,
            "configuration_sha256": resolved.configuration_sha256,
        },
    ).collect(
        device_serial=args.device_serial,
        environment_id=args.environment_id,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
