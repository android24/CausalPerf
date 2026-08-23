from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from export_public_task import export

from .backends import BackendUnavailable, IsolationBackend, ProcessOutcome, select_backend
from .model import IsolationContractError, IsolationPolicy, IsolationRunSpec, PrivateCanarySet, seal_report
from .scanner import ScanResult, scan_tree, scan_values, tree_digest


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _merge(phase: str, *results: ScanResult) -> ScanResult:
    return ScanResult(
        phase,
        tuple(sorted({code for result in results for code in result.finding_codes})),
        sum(result.files_scanned for result in results),
        sum(result.bytes_scanned for result in results),
    )


def _make_writable(path: Path) -> None:
    if not path.exists():
        raise IsolationContractError(f"declared writable path does not exist: {path}")
    targets = [path, *path.rglob("*")] if path.is_dir() else [path]
    for target in targets:
        mode = target.stat().st_mode
        if target.is_dir():
            target.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        elif target.is_file():
            target.chmod(mode | stat.S_IRUSR | stat.S_IWUSR)


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = path.resolve()
    return any(candidate == root.resolve() or root.resolve() in candidate.parents for root in roots)


class IsolationHarness:
    def __init__(self, policy: IsolationPolicy, canaries: PrivateCanarySet,
                 *, backend: IsolationBackend | None = None,
                 clock: Callable[[], str] = _utc_now):
        self.policy = policy
        self.canaries = canaries
        self.backend = backend
        self.clock = clock

    def _base_report(self, spec: IsolationRunSpec, started_at: str) -> dict:
        return {
            "schema_version": 2,
            "id": f"IR-{self.policy.digest[:16].upper()}",
            "run_id": self.policy.run_id,
            "task_id": spec.task_id,
            "started_at": started_at,
            "completed_at": self.clock(),
            "backend": self.backend.name if self.backend else "UNAVAILABLE",
            "status": "UNSUPPORTED",
            "controls": {
                "network_denied": bool(self.backend and self.backend.network_denied),
                "separate_views": bool(self.backend and self.backend.separate_views),
                "environment_allowlisted": True,
                "owned_process_group": bool(self.backend and self.backend.owned_process_group),
            },
            "scans": [],
            "reason_codes": [],
            "artifact_digests": [],
        }

    def _validate_layout(self, spec: IsolationRunSpec) -> None:
        public = spec.public_source.resolve()
        private = spec.private_evaluator.resolve()
        run_root = spec.run_root.resolve()
        if any(_overlap(left, right) for left, right in (
            (public, private), (public, run_root), (private, run_root),
        )):
            raise IsolationContractError("public, private and run roots must be disjoint")
        runtime_roots = [Path(item).resolve() for item in self.policy.get("runtime_read_paths")]
        if any(root == Path("/") or _overlap(root, private) for root in runtime_roots):
            raise IsolationContractError("runtime read scope would expose the private evaluator")
        denied_roots = [Path(item).resolve() for item in self.policy.get("host_denied_read_paths")]
        if any(root == Path("/") for root in denied_roots):
            raise IsolationContractError("host denied roots must be narrower than filesystem root")
        if not any(private == root or root in private.parents for root in denied_roots):
            raise IsolationContractError("host denied roots do not contain the private evaluator")
        executables = [Path(item).resolve() for item in self.policy.get("allowed_executables")]
        if any(executable == private or private in executable.parents for executable in executables):
            raise IsolationContractError("an allowlisted executable is inside the private evaluator")
        private_text = os.path.normcase(str(private))
        if any(private_text in os.path.normcase(value) for value in (
            *spec.agent_command.args,
            *spec.agent_command.environment.values(),
        )):
            raise IsolationContractError("Agent command metadata reveals the private evaluator path")
        if self.policy.run_id == "" or spec.task_id != self.canaries.task_id or spec.task_version != self.canaries.task_version:
            raise IsolationContractError("run/task/canary identity mismatch")
        self.policy_limits = self.policy.get("limits")
        spec.agent_command.validate(self.policy, evaluator=False)
        spec.evaluator_command.validate(self.policy, evaluator=True)

    def _preflight_scans(self, spec: IsolationRunSpec) -> tuple[ScanResult, ScanResult]:
        limits = self.policy.get("limits")
        tree = scan_tree(
            spec.public_source, self.canaries.values, "PRE_INPUT",
            max_files=limits["scan_files"], max_bytes=limits["scan_bytes"],
        )
        command_values = [
            spec.agent_command.executable, *spec.agent_command.args,
            spec.evaluator_command.executable, *spec.evaluator_command.args,
        ]
        command_scan = scan_values(command_values, self.canaries.values, "PRE_INPUT")
        environment = [
            *(f"{key}={value}" for key, value in spec.agent_command.environment.items()),
            *(f"{key}={value}" for key, value in spec.evaluator_command.environment.items()),
        ]
        return _merge("PRE_INPUT", tree, command_scan), scan_values(
            environment, self.canaries.values, "PRE_ENVIRONMENT"
        )

    def _prepare_workspace(self, spec: IsolationRunSpec) -> tuple[Path, Path, Path, Path]:
        if spec.run_root.exists():
            raise IsolationContractError("run root already exists")
        spec.run_root.mkdir(parents=True)
        workspace = spec.run_root / "agent-view" / "workspace"
        agent_output = spec.run_root / "agent-output"
        evaluator_output = spec.run_root / "evaluator-private-output"
        agent_logs = spec.run_root / "agent-logs"
        export(spec.public_source, workspace)
        agent_output.mkdir()
        evaluator_output.mkdir()
        agent_logs.mkdir()
        for relative in self.policy.get("writable_paths"):
            _make_writable(workspace / relative)
        return workspace, agent_output, evaluator_output, agent_logs

    def _process_reasons(self, outcome: ProcessOutcome, role: str) -> list[str]:
        reasons: list[str] = []
        if outcome.timed_out:
            reasons.append(f"{role}_PROCESS_TIMEOUT")
        elif outcome.exit_code != 0:
            reasons.append(f"{role}_PROCESS_FAILED")
        if outcome.output_limit_exceeded:
            reasons.append("OUTPUT_LIMIT_EXCEEDED")
        return reasons

    def run(self, spec: IsolationRunSpec) -> dict:
        started_at = self.clock()
        self._validate_layout(spec)
        pre_input, pre_environment = self._preflight_scans(spec)
        scans = [pre_input, pre_environment]

        report = self._base_report(spec, started_at)
        if not all(item.passed for item in scans):
            report.update({
                "status": "LEAK_DETECTED",
                "scans": [item.to_dict() for item in scans],
                "reason_codes": ["LEAKAGE_SCAN_FAILED"],
                "completed_at": self.clock(),
            })
            return seal_report(report)

        if self.backend is None:
            try:
                self.backend = select_backend(self.policy)
            except BackendUnavailable as error:
                report.update({
                    "status": "UNSUPPORTED",
                    "backend": "UNAVAILABLE",
                    "scans": [item.to_dict() for item in scans],
                    "reason_codes": [error.reason_code],
                    "completed_at": self.clock(),
                })
                return seal_report(report)

        report["backend"] = self.backend.name
        report["controls"] = {
            "network_denied": self.backend.network_denied,
            "separate_views": self.backend.separate_views,
            "environment_allowlisted": True,
            "owned_process_group": self.backend.owned_process_group,
        }
        if not all(report["controls"].values()):
            report.update({
                "status": "UNSUPPORTED",
                "scans": [item.to_dict() for item in scans],
                "reason_codes": ["ISOLATION_BACKEND_PROBE_FAILED"],
                "completed_at": self.clock(),
            })
            return seal_report(report)

        workspace, agent_output, evaluator_output, agent_logs = self._prepare_workspace(spec)
        agent_roots = (workspace, agent_output)
        if not _inside(spec.agent_command.working_directory.resolve(), agent_roots):
            raise IsolationContractError("agent working directory is outside Agent view")
        protected_before = {
            path: tree_digest(workspace / path) for path in self.policy.get("protected_paths")
        }
        agent_stdout = agent_logs / "stdout.log"
        agent_stderr = agent_logs / "stderr.log"
        try:
            agent = self.backend.run(
                spec.agent_command,
                read_roots=(workspace,),
                write_roots=tuple(workspace / item for item in self.policy.get("writable_paths")) + (agent_output,),
                policy=self.policy,
                stdout_path=agent_stdout,
                stderr_path=agent_stderr,
            )
        except BackendUnavailable as error:
            reason = error.reason_code if error.reason_code in {
                "ISOLATION_BACKEND_UNAVAILABLE", "ISOLATION_BACKEND_PROBE_FAILED",
            } else "ISOLATION_BACKEND_PROBE_FAILED"
            report.update({
                "status": "UNSUPPORTED",
                "scans": [item.to_dict() for item in scans],
                "reason_codes": [reason],
                "completed_at": self.clock(),
            })
            return seal_report(report)
        report["agent_exit_code"] = agent.exit_code
        report["artifact_digests"].extend((agent.stdout_sha256, agent.stderr_sha256))
        reasons = self._process_reasons(agent, "AGENT")

        limits = self.policy.get("limits")
        scans.extend((
            scan_tree(workspace, self.canaries.values, "POST_WORKSPACE", max_files=limits["scan_files"], max_bytes=limits["scan_bytes"]),
            scan_tree(agent_output, self.canaries.values, "POST_OUTPUT", max_files=limits["scan_files"], max_bytes=limits["scan_bytes"]),
            scan_tree(agent_logs, self.canaries.values, "POST_LOG", max_files=limits["scan_files"], max_bytes=limits["scan_bytes"]),
        ))
        protected_changed = any(
            tree_digest(workspace / path) != expected
            for path, expected in protected_before.items()
        )
        if protected_changed:
            reasons.append("PROTECTED_PATH_CHANGED")
        if not all(item.passed for item in scans):
            reasons.append("LEAKAGE_SCAN_FAILED")
            report["status"] = "LEAK_DETECTED"
        elif reasons:
            report["status"] = "AGENT_FAILED"
        else:
            evaluator_roots = (workspace, agent_output, agent_logs, spec.private_evaluator.resolve(), evaluator_output)
            if not _inside(spec.evaluator_command.working_directory.resolve(), evaluator_roots):
                raise IsolationContractError("evaluator working directory is outside evaluator view")
            evaluator_logs = spec.run_root / "evaluator-private-logs"
            evaluator_logs.mkdir()
            try:
                evaluator = self.backend.run(
                    spec.evaluator_command,
                    read_roots=(workspace, agent_output, agent_logs, spec.private_evaluator.resolve()),
                    write_roots=(evaluator_output,),
                    policy=self.policy,
                    stdout_path=evaluator_logs / "stdout.log",
                    stderr_path=evaluator_logs / "stderr.log",
                )
            except BackendUnavailable as error:
                reason = error.reason_code if error.reason_code in {
                    "ISOLATION_BACKEND_UNAVAILABLE", "ISOLATION_BACKEND_PROBE_FAILED",
                } else "ISOLATION_BACKEND_PROBE_FAILED"
                report.update({
                    "status": "UNSUPPORTED",
                    "scans": [item.to_dict() for item in scans],
                    "reason_codes": [reason],
                    "completed_at": self.clock(),
                })
                return seal_report(report)
            report["evaluator_exit_code"] = evaluator.exit_code
            report["artifact_digests"].extend((evaluator.stdout_sha256, evaluator.stderr_sha256))
            reasons.extend(self._process_reasons(evaluator, "EVALUATOR"))
            evaluator_scan = _merge(
                "POST_EVALUATOR",
                scan_tree(evaluator_output, self.canaries.values, "POST_EVALUATOR", max_files=limits["scan_files"], max_bytes=limits["scan_bytes"]),
                scan_tree(evaluator_logs, self.canaries.values, "POST_EVALUATOR", max_files=limits["scan_files"], max_bytes=limits["scan_bytes"]),
            )
            scans.append(evaluator_scan)
            if not evaluator_scan.passed:
                reasons.append("LEAKAGE_SCAN_FAILED")
                report["status"] = "LEAK_DETECTED"
            elif reasons:
                report["status"] = "EVALUATOR_FAILED"
            else:
                report["status"] = "PASS"

        report["scans"] = [item.to_dict() for item in scans]
        report["reason_codes"] = list(dict.fromkeys(reasons))
        report["artifact_digests"] = list(dict.fromkeys(report["artifact_digests"]))
        report["completed_at"] = self.clock()
        return seal_report(report)
