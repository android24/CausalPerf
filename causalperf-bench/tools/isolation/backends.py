from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .model import CommandSpec, IsolationPolicy

try:
    import resource
except ImportError:  # pragma: no cover - unsupported hosts fail in selection
    resource = None


class BackendUnavailable(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    timed_out: bool
    output_limit_exceeded: bool
    stdout_sha256: str
    stderr_sha256: str


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IsolationBackend:
    name = "UNAVAILABLE"
    network_denied = False
    separate_views = False
    owned_process_group = False

    def probe(self, policy: IsolationPolicy) -> bool:
        raise NotImplementedError

    def wrap(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
             write_roots: tuple[Path, ...], policy: IsolationPolicy) -> list[str]:
        raise NotImplementedError

    def run(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
            write_roots: tuple[Path, ...], policy: IsolationPolicy,
            stdout_path: Path, stderr_path: Path) -> ProcessOutcome:
        effective_write_roots = tuple(dict.fromkeys(
            (*write_roots, stdout_path.parent, stderr_path.parent)
        ))
        wrapped = self.wrap(
            command, read_roots=read_roots, write_roots=effective_write_roots,
            policy=policy,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        limit = policy.get("limits")["output_bytes"]
        timeout = policy.get("limits")["wall_time_seconds"]
        timed_out = False

        def apply_limits() -> None:
            if resource is None:
                raise RuntimeError("resource limits unavailable")
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                wrapped,
                cwd=command.working_directory,
                env=command.environment,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                preexec_fn=apply_limits,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        output_limit_exceeded = stdout_path.stat().st_size + stderr_path.stat().st_size > limit
        return ProcessOutcome(
            exit_code=process.returncode,
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            stdout_sha256=_file_digest(stdout_path),
            stderr_sha256=_file_digest(stderr_path),
        )


class DarwinSandboxBackend(IsolationBackend):
    name = "DARWIN_SANDBOX"
    network_denied = True
    separate_views = True
    owned_process_group = True

    def __init__(self, executable: str = "/usr/bin/sandbox-exec"):
        self.executable = executable

    @staticmethod
    def _literal(path: str | Path) -> str:
        return json.dumps(str(path))

    def _profile(self, *, read_roots: tuple[Path, ...], write_roots: tuple[Path, ...],
                 policy: IsolationPolicy) -> str:
        runtime_roots = [Path(item) for item in policy.get("runtime_read_paths") if Path(item).exists()]
        executable_roots = [Path(item) for item in policy.get("allowed_executables")]
        lines = [
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            "(deny file-write*)",
            "(deny process-exec)",
            "(deny signal)",
            "(allow signal (target self))",
        ]
        for path in sorted(Path(item) for item in policy.get("host_denied_read_paths")):
            lines.append(f"(deny file-read* (subpath {self._literal(path)}))")
        for path in sorted(set(runtime_roots + list(read_roots) + list(write_roots))):
            lines.append(f"(allow file-read* (subpath {self._literal(path)}))")
        for path in sorted(set(executable_roots)):
            lines.append(f"(allow file-read* (literal {self._literal(path)}))")
        for executable in policy.get("allowed_executables"):
            lines.append(f"(allow process-exec (literal {self._literal(executable)}))")
        for path in sorted(set(write_roots)):
            lines.append(f"(allow file-write* (subpath {self._literal(path)}))")
        return "\n".join(lines) + "\n"

    def probe(self, policy: IsolationPolicy) -> bool:
        if platform.system() != "Darwin" or not Path(self.executable).is_file():
            return False
        profile = "(version 1)\n(allow default)\n(deny network*)\n"
        result = subprocess.run(
            [self.executable, "-p", profile, "/usr/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def wrap(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
             write_roots: tuple[Path, ...], policy: IsolationPolicy) -> list[str]:
        profile = self._profile(read_roots=read_roots, write_roots=write_roots, policy=policy)
        return [self.executable, "-p", profile, command.executable, *command.args]


class LinuxBubblewrapBackend(IsolationBackend):
    name = "LINUX_BWRAP"
    network_denied = True
    separate_views = True
    owned_process_group = True

    def __init__(self, executable: str | None = None):
        self.executable = executable or shutil.which("bwrap") or ""

    def probe(self, policy: IsolationPolicy) -> bool:
        if platform.system() != "Linux" or not self.executable:
            return False
        result = subprocess.run(
            [self.executable, "--unshare-all", "--new-session", "--ro-bind", "/", "/", "/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def wrap(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
             write_roots: tuple[Path, ...], policy: IsolationPolicy) -> list[str]:
        if not self.executable:
            raise BackendUnavailable("ISOLATION_BACKEND_UNAVAILABLE")
        wrapped = [
            self.executable, "--die-with-parent", "--new-session", "--unshare-all",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--clearenv",
        ]
        for system_root in policy.get("runtime_read_paths"):
            if Path(system_root).exists():
                wrapped.extend(("--ro-bind", system_root, system_root))
        for executable in policy.get("allowed_executables"):
            if Path(executable).exists() and not any(
                Path(executable) == Path(root) or Path(root) in Path(executable).parents
                for root in policy.get("runtime_read_paths")
            ):
                wrapped.extend(("--ro-bind", executable, executable))
        for root in sorted(set(read_roots)):
            wrapped.extend(("--ro-bind", str(root), str(root)))
        for root in sorted(set(write_roots)):
            wrapped.extend(("--bind", str(root), str(root)))
        for key, value in sorted(command.environment.items()):
            wrapped.extend(("--setenv", key, value))
        wrapped.extend(("--chdir", str(command.working_directory), command.executable, *command.args))
        return wrapped


@dataclass(frozen=True)
class _WindowsFolderMapping:
    host: Path
    sandbox: PureWindowsPath
    read_only: bool


class WindowsSandboxBackend(IsolationBackend):
    """Disposable-VM backend for Windows 11 24H2+ Windows Sandbox.

    The complete input view is mapped read-only. Any read root containing an
    allowed writable subtree is copied into the disposable VM first; only the
    declared writable subtree is synchronized back to a dedicated writable
    mapping after the command exits. This avoids exposing the host workspace as
    one broad writable mapped folder.
    """

    name = "WINDOWS_SANDBOX"
    network_denied = True
    separate_views = True
    owned_process_group = True
    _SANDBOX_ROOT = PureWindowsPath(r"C:\CausalPerf")

    def __init__(self, executable: str | None = None):
        windows_root = os.environ.get("WINDIR", r"C:\Windows")
        self.executable = executable or shutil.which("wsb.exe") or str(
            PureWindowsPath(windows_root) / "System32" / "wsb.exe"
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _relative(path: Path, root: Path) -> Path:
        return path.resolve().relative_to(root.resolve())

    @staticmethod
    def _sandbox_join(root: PureWindowsPath, relative: Path) -> PureWindowsPath:
        return root.joinpath(*relative.parts)

    @staticmethod
    def _native_windows_path(value: str) -> bool:
        candidate = PureWindowsPath(value)
        if not candidate.is_absolute():
            return False
        windows_root = PureWindowsPath(os.environ.get("WINDIR", r"C:\Windows"))
        try:
            candidate.relative_to(windows_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _deduplicate(paths: tuple[Path, ...] | list[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            resolved = path.resolve()
            key = os.path.normcase(str(resolved))
            if key not in seen:
                seen.add(key)
                result.append(resolved)
        return result

    @staticmethod
    def _translate(value: str, translations: list[tuple[Path, PureWindowsPath]],
                   *, required: bool = False) -> str:
        candidate = Path(value)
        if candidate.is_absolute():
            for host, sandbox in sorted(
                translations, key=lambda item: len(item[0].parts), reverse=True
            ):
                try:
                    relative = candidate.resolve().relative_to(host.resolve())
                except ValueError:
                    continue
                return str(sandbox.joinpath(*relative.parts))
        if WindowsSandboxBackend._native_windows_path(value):
            return str(PureWindowsPath(value))
        if required:
            raise BackendUnavailable("WINDOWS_SANDBOX_PATH_NOT_MAPPED")
        result = value
        for host, sandbox in sorted(
            translations, key=lambda item: len(str(item[0])), reverse=True
        ):
            result = re.sub(
                re.escape(str(host)), lambda _: str(sandbox), result,
                flags=re.IGNORECASE,
            )
        return result

    @classmethod
    def _configuration_xml(cls, mappings: list[_WindowsFolderMapping]) -> str:
        configuration = ET.Element("Configuration")
        for name, value in (
            ("VGpu", "Disable"),
            ("Networking", "Disable"),
            ("AudioInput", "Disable"),
            ("VideoInput", "Disable"),
            ("ProtectedClient", "Enable"),
            ("PrinterRedirection", "Disable"),
            ("ClipboardRedirection", "Disable"),
        ):
            ET.SubElement(configuration, name).text = value
        mapped_folders = ET.SubElement(configuration, "MappedFolders")
        for mapping in mappings:
            element = ET.SubElement(mapped_folders, "MappedFolder")
            ET.SubElement(element, "HostFolder").text = str(mapping.host)
            ET.SubElement(element, "SandboxFolder").text = str(mapping.sandbox)
            ET.SubElement(element, "ReadOnly").text = "true" if mapping.read_only else "false"
        logon = ET.SubElement(configuration, "LogonCommand")
        ET.SubElement(logon, "Command").text = (
            r"powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            r"-File C:\CausalPerf\Control\bootstrap.ps1"
        )
        return ET.tostring(configuration, encoding="unicode", xml_declaration=False)

    @staticmethod
    def _bootstrap_script() -> str:
        return r'''$ErrorActionPreference = "Stop"
$resultRoot = "C:\CausalPerf\Result"
$statusPath = Join-Path $resultRoot "status.json"
$statusTemp = Join-Path $resultRoot "status.tmp.json"
$stderrPath = Join-Path $resultRoot "stderr.log"
$exitCode = 125

function Invoke-DirectoryMirror([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & "C:\Windows\System32\robocopy.exe" $Source $Destination /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

try {
    $spec = Get-Content -Raw "C:\CausalPerf\Control\command.json" | ConvertFrom-Json
    foreach ($copy in $spec.stage_copies) {
        Invoke-DirectoryMirror $copy.source $copy.destination
    }
    Get-ChildItem Env: | ForEach-Object { Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue }
    foreach ($property in $spec.environment.PSObject.Properties) {
        Set-Item ("Env:" + $property.Name) ([string]$property.Value)
    }
    New-Item -ItemType Directory -Force -Path "C:\CausalPerf\Temp" | Out-Null
    Set-Item Env:SystemRoot "C:\Windows"
    Set-Item Env:SystemDrive "C:"
    Set-Item Env:TEMP "C:\CausalPerf\Temp"
    Set-Item Env:TMP "C:\CausalPerf\Temp"
    Set-Location $spec.working_directory
    & $spec.executable @($spec.args) 1> $spec.stdout_path 2> $spec.stderr_path
    if ($null -eq $LASTEXITCODE) { $exitCode = 0 } else { $exitCode = [int]$LASTEXITCODE }
    foreach ($copy in $spec.copy_back) {
        Invoke-DirectoryMirror $copy.source $copy.destination
    }
} catch {
    $_ | Out-String | Add-Content -Path $stderrPath
    $exitCode = 125
} finally {
    @{ exit_code = $exitCode } | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 $statusTemp
    Move-Item -Force $statusTemp $statusPath
    & "C:\Windows\System32\shutdown.exe" /s /f /t 0
}
'''

    def _build_launch(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
                      write_roots: tuple[Path, ...], policy: IsolationPolicy,
                      control: Path, result: Path) -> tuple[list[_WindowsFolderMapping], dict]:
        reads = self._deduplicate(list(read_roots) + [
            Path(item) for item in policy.get("runtime_read_paths") if Path(item).exists()
        ])
        writes = self._deduplicate(list(write_roots))
        if any(not path.is_dir() for path in (*reads, *writes)):
            raise BackendUnavailable("WINDOWS_SANDBOX_REQUIRES_DIRECTORY_ROOTS")

        mappings: list[_WindowsFolderMapping] = []
        translations: list[tuple[Path, PureWindowsPath]] = []
        stage_copies: list[dict[str, str]] = []
        copy_back: list[dict[str, str]] = []

        for index, read in enumerate(reads):
            mapped = self._SANDBOX_ROOT / "Input" / f"Read{index}"
            nested_writes = [write for write in writes if self._is_within(write, read)]
            effective = mapped
            mappings.append(_WindowsFolderMapping(read, mapped, True))
            if nested_writes:
                effective = self._SANDBOX_ROOT / "Local" / f"Read{index}"
                stage_copies.append({"source": str(mapped), "destination": str(effective)})
            translations.append((read, effective))

        for index, write in enumerate(writes):
            containing = next((read for read in reads if self._is_within(write, read)), None)
            mapped = self._SANDBOX_ROOT / "WriteBack" / f"Write{index}"
            mappings.append(_WindowsFolderMapping(write, mapped, False))
            if containing is not None:
                effective_root = next(value for root, value in translations if root == containing)
                local_source = self._sandbox_join(effective_root, self._relative(write, containing))
                copy_back.append({"source": str(local_source), "destination": str(mapped)})
            else:
                translations.append((write, mapped))

        mappings.extend((
            _WindowsFolderMapping(control.resolve(), self._SANDBOX_ROOT / "Control", True),
            _WindowsFolderMapping(result.resolve(), self._SANDBOX_ROOT / "Result", False),
        ))
        executable = self._translate(command.executable, translations, required=True)
        working_directory = self._translate(
            str(command.working_directory.resolve()), translations, required=True
        )
        return mappings, {
            "executable": executable,
            "args": [self._translate(item, translations) for item in command.args],
            "working_directory": working_directory,
            "environment": {
                key: self._translate(value, translations)
                for key, value in command.environment.items()
            },
            "stage_copies": stage_copies,
            "copy_back": copy_back,
            "stdout_path": str(self._SANDBOX_ROOT / "Result" / "stdout.log"),
            "stderr_path": str(self._SANDBOX_ROOT / "Result" / "stderr.log"),
        }

    def probe(self, policy: IsolationPolicy) -> bool:
        if platform.system() != "Windows" or not Path(self.executable).is_file():
            return False
        dism = shutil.which("dism.exe") or shutil.which("dism")
        if dism:
            result = subprocess.run(
                [dism, "/Online", "/English", "/Get-FeatureInfo",
                 "/FeatureName:Containers-DisposableClientVM"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            if result.returncode != 0 or "State : Enabled" not in result.stdout:
                return False
        try:
            result = subprocess.run(
                [self.executable, "list"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def wrap(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
             write_roots: tuple[Path, ...], policy: IsolationPolicy) -> list[str]:
        raise BackendUnavailable("WINDOWS_SANDBOX_REQUIRES_LAUNCH_PLAN")

    def _terminate(self, process: subprocess.Popen, sandbox_id: str) -> None:
        try:
            subprocess.run(
                [self.executable, "stop", "--id", sandbox_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            return
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def run(self, command: CommandSpec, *, read_roots: tuple[Path, ...],
            write_roots: tuple[Path, ...], policy: IsolationPolicy,
            stdout_path: Path, stderr_path: Path) -> ProcessOutcome:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        limit = policy.get("limits")["output_bytes"]
        timeout = policy.get("limits")["wall_time_seconds"]
        timed_out = False
        output_limit_exceeded = False

        with tempfile.TemporaryDirectory(prefix=".causalperf-wsb-", dir=stdout_path.parent) as temporary:
            temporary_root = Path(temporary)
            control = temporary_root / "control"
            result = temporary_root / "result"
            control.mkdir()
            result.mkdir()
            sandbox_stdout = result / "stdout.log"
            sandbox_stderr = result / "stderr.log"
            sandbox_stdout.write_bytes(b"")
            sandbox_stderr.write_bytes(b"")
            mappings, specification = self._build_launch(
                command, read_roots=read_roots, write_roots=write_roots,
                policy=policy, control=control, result=result,
            )
            (control / "bootstrap.ps1").write_text(self._bootstrap_script(), encoding="utf-8")
            (control / "command.json").write_text(
                json.dumps(specification, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            configuration = temporary_root / "evaluation.wsb"
            configuration_xml = self._configuration_xml(mappings)
            configuration.write_text(configuration_xml, encoding="utf-8")
            if len(configuration_xml) > 30000:
                raise BackendUnavailable("WINDOWS_SANDBOX_CONFIGURATION_TOO_LARGE")
            sandbox_id = str(uuid.uuid4())
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(
                [self.executable, "start", "--id", sandbox_id, "--config", configuration_xml],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            deadline = time.monotonic() + timeout
            status_path = result / "status.json"
            while not status_path.is_file():
                if sandbox_stdout.stat().st_size + sandbox_stderr.stat().st_size > limit:
                    output_limit_exceeded = True
                    self._terminate(process, sandbox_id)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate(process, sandbox_id)
                    break
                if process.poll() not in (None, 0):
                    break
                time.sleep(0.1)

            if status_path.is_file():
                try:
                    exit_code = int(json.loads(status_path.read_text(encoding="utf-8-sig"))["exit_code"])
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    exit_code = 125
                self._terminate(process, sandbox_id)
            else:
                exit_code = process.returncode if process.returncode is not None else 125

            output_limit_exceeded = output_limit_exceeded or (
                sandbox_stdout.stat().st_size + sandbox_stderr.stat().st_size > limit
            )
            shutil.copyfile(sandbox_stdout, stdout_path)
            shutil.copyfile(sandbox_stderr, stderr_path)

        return ProcessOutcome(
            exit_code=exit_code,
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            stdout_sha256=_file_digest(stdout_path),
            stderr_sha256=_file_digest(stderr_path),
        )


def select_backend(policy: IsolationPolicy) -> IsolationBackend:
    requested = policy.get("backend")
    candidates: list[IsolationBackend] = []
    if requested in {"AUTO", "DARWIN_SANDBOX"}:
        candidates.append(DarwinSandboxBackend())
    if requested in {"AUTO", "LINUX_BWRAP"}:
        candidates.append(LinuxBubblewrapBackend())
    if requested in {"AUTO", "WINDOWS_SANDBOX"}:
        candidates.append(WindowsSandboxBackend())
    installed = [item for item in candidates if (
        (item.name == "DARWIN_SANDBOX" and Path(item.executable).is_file())
        or (item.name == "LINUX_BWRAP" and bool(item.executable))
        or (item.name == "WINDOWS_SANDBOX" and Path(item.executable).is_file())
    )]
    if not installed:
        raise BackendUnavailable("ISOLATION_BACKEND_UNAVAILABLE")
    for backend in installed:
        if backend.probe(policy):
            return backend
    raise BackendUnavailable("ISOLATION_BACKEND_PROBE_FAILED")
