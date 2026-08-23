from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Callable

from causalperf_reference.artifacts import ContractError, parse_time, verify_content_digest

from causalperf_agent.execution.model import ExecutionSnapshot

from .model import (
    PolicyDecision, RuntimePolicy, ToolRequest, validate_approval_record,
    validate_tool_request,
)


CAPABILITIES = {
    "inspect_source": "R0", "query_trace": "R0", "inspect_device": "R0",
    "build_variant": "R1", "install_apk": "R1", "run_benchmark": "R1",
    "apply_patch": "R2", "rollback_patch": "R2", "publish_patch": "R4",
}
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
CONTROL_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "<<"}


def _within(path: str, root: str) -> bool:
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(root.rstrip("/"))
    if not path or ".." in candidate.parts or candidate.is_absolute() != boundary.is_absolute():
        return False
    if str(boundary) == ".":
        return not candidate.is_absolute() and ".." not in candidate.parts
    return candidate == boundary or boundary in candidate.parents


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PolicyEngine:
    def __init__(self, policy: RuntimePolicy, approvals: list[dict] | None = None,
                 *, clock: Callable[[], str] = _utc_now):
        self.policy = policy
        self.approvals = {item["id"]: item for item in (approvals or [])}
        self.clock = clock

    def _deny(self, request: ToolRequest, risk: str, *reasons: str) -> PolicyDecision:
        return PolicyDecision("DENY", risk, request.request_sha256, tuple(reasons))

    def _validate_command(self, request: ToolRequest, command: dict) -> tuple[str, ...]:
        reasons: list[str] = []
        executable = command.get("executable", "")
        basename = PurePosixPath(executable).name.lower()
        allowed = self.policy.get("allowed_executables").get(request.tool_id, [])
        if executable not in allowed:
            reasons.append("EXECUTABLE_NOT_ALLOWED")
        if basename in SHELL_EXECUTABLES:
            reasons.append("SHELL_EXECUTABLE_FORBIDDEN")
        for argument in command.get("args", []):
            if argument in CONTROL_TOKENS or any(token in argument for token in ("$(", "`", "\n", "\x00")):
                reasons.append("UNSAFE_COMMAND_ARGUMENT")
                break
            if "*" in argument or "?" in argument:
                reasons.append("UNRESOLVED_GLOB_FORBIDDEN")
                break
        working = command.get("working_directory", "")
        if not any(_within(working, root) for root in self.policy.get("allowed_working_directories")):
            reasons.append("WORKING_DIRECTORY_NOT_ALLOWED")
        allowed_environment = set(self.policy.get("allowed_environment_keys"))
        if set(command.get("environment", {})) - allowed_environment:
            reasons.append("ENVIRONMENT_KEY_NOT_ALLOWED")
        return tuple(reasons)

    def _scope_reasons(self, request: ToolRequest, snapshot: ExecutionSnapshot) -> tuple[str, ...]:
        arguments = request.arguments
        reasons: list[str] = []
        if request.tool_id == "inspect_source":
            root = arguments.get("root", ".")
            for path in arguments.get("paths", []):
                resolved = path if root == "." else f"{root.rstrip('/')}/{path}"
                if not any(_within(resolved, allowed) for allowed in self.policy.get("readable_paths")):
                    reasons.append("READ_PATH_NOT_ALLOWED")
                    break
        elif request.tool_id == "build_variant":
            reasons.extend(self._validate_command(request, arguments))
        elif request.tool_id == "install_apk":
            if arguments.get("device_serial_hash") != self.policy.get("device_serial_hash"):
                reasons.append("DEVICE_NOT_ALLOWED")
            if arguments.get("package_name") != self.policy.get("package_name"):
                reasons.append("PACKAGE_NOT_ALLOWED")
        elif request.tool_id == "run_benchmark":
            reasons.extend(self._validate_command(request, arguments.get("command", {})))
            if arguments.get("device_serial_hash") != self.policy.get("device_serial_hash"):
                reasons.append("DEVICE_NOT_ALLOWED")
            if arguments.get("package_name") != self.policy.get("package_name"):
                reasons.append("PACKAGE_NOT_ALLOWED")
            if arguments.get("partition") not in self.policy.get("allowed_partitions"):
                reasons.append("PARTITION_NOT_ALLOWED")
        elif request.tool_id == "apply_patch":
            writable = self.policy.get("writable_paths")
            protected = self.policy.get("protected_paths")
            declared = arguments.get("allowed_paths", [])
            changed = arguments.get("changed_paths", [])
            if any(not any(_within(path, root) for root in writable) for path in declared):
                reasons.append("DECLARED_WRITE_SCOPE_NOT_ALLOWED")
            if any(not any(_within(path, root) for root in declared) for path in changed):
                reasons.append("PATCH_OUTSIDE_DECLARED_SCOPE")
            if any(any(_within(path, root) for root in protected) for path in changed):
                reasons.append("PROTECTED_PATH_MUTATION")
        elif request.tool_id == "rollback_patch":
            if arguments.get("intervention_id") not in snapshot.rollback_obligations:
                reasons.append("ROLLBACK_OBLIGATION_NOT_FOUND")
        elif request.tool_id == "publish_patch" and not self.policy.get("allow_external_publication"):
            reasons.append("EXTERNAL_PUBLICATION_PROHIBITED")
        return tuple(dict.fromkeys(reasons))

    def _budget_delta(self, request: ToolRequest) -> dict[str, int]:
        arguments = request.arguments
        command = arguments.get("command", arguments)
        return {
            "tool_calls": 1,
            "wall_time_seconds": int(command.get("timeout_seconds", arguments.get("timeout_seconds", 0))),
            "experiments": 1 if request.tool_id == "run_benchmark" else 0,
            "patch_files": len(arguments.get("changed_paths", [])) if request.tool_id == "apply_patch" else 0,
            "patch_lines": int(arguments.get("changed_line_count", 0)) if request.tool_id == "apply_patch" else 0,
            "output_bytes": int(command.get("output_limit_bytes", 0)),
        }

    def _approval(self, request: ToolRequest, risk: str) -> tuple[str | None, tuple[str, ...]]:
        approval_id = request.arguments.get("approval_id")
        if not approval_id:
            return None, ("APPROVAL_ID_REQUIRED",)
        approval = self.approvals.get(approval_id)
        if approval is None:
            return approval_id, ("APPROVAL_REQUIRED",)
        try:
            validate_approval_record(approval)
            verify_content_digest(approval)
        except ContractError:
            return approval_id, ("APPROVAL_RECORD_INVALID",)
        reasons: list[str] = []
        if approval["run_id"] != self.policy.run_id or approval["risk"] != risk:
            reasons.append("APPROVAL_SCOPE_MISMATCH")
        if approval["request_sha256"] != request.request_sha256:
            reasons.append("APPROVAL_REQUEST_MISMATCH")
        if approval["decision"] != "APPROVED":
            reasons.append("APPROVAL_NOT_ACTIVE")
        if request.authorization_at is None:
            reasons.append("AUTHORIZATION_TIME_REQUIRED")
            return approval_id, tuple(reasons)
        authorization_at = request.authorization_at
        try:
            if parse_time(approval["decided_at"]) > parse_time(authorization_at):
                reasons.append("APPROVAL_RECORDED_AFTER_AUTHORIZATION")
            if approval.get("expires_at") and parse_time(approval["expires_at"]) <= parse_time(authorization_at):
                reasons.append("APPROVAL_EXPIRED")
        except (TypeError, ValueError):
            reasons.append("APPROVAL_TIME_INVALID")
        return approval_id, tuple(reasons)

    def evaluate(self, request: ToolRequest, snapshot: ExecutionSnapshot) -> PolicyDecision:
        risk = CAPABILITIES.get(request.tool_id, "UNKNOWN")
        if risk == "UNKNOWN":
            return self._deny(request, risk, "UNKNOWN_TOOL")
        if snapshot.run_id != self.policy.run_id:
            return self._deny(request, risk, "RUN_ID_MISMATCH")
        if snapshot.policy_sha256 not in {None, self.policy.digest}:
            return self._deny(request, risk, "POLICY_DIGEST_MISMATCH")
        try:
            validate_tool_request(request)
        except ContractError:
            return self._deny(request, risk, "MALFORMED_TOOL_REQUEST")
        scope_reasons = self._scope_reasons(request, snapshot)
        if scope_reasons:
            return self._deny(request, risk, *scope_reasons)

        approval_id = None
        if request.tool_id == "rollback_patch" and request.arguments.get("intervention_id") in snapshot.rollback_obligations:
            pass
        elif risk in self.policy.get("task_approved_risks"):
            pass
        else:
            approval_id, reasons = self._approval(request, risk)
            if reasons:
                status = "REQUIRE_APPROVAL" if reasons == ("APPROVAL_REQUIRED",) else "DENY"
                return PolicyDecision(status, risk, request.request_sha256, reasons, approval_id)

        delta = self._budget_delta(request)
        limits = self.policy.get("budgets")
        exceeded = [name for name, amount in delta.items() if snapshot.budget_usage.get(name, 0) + amount > limits[name]]
        if exceeded and request.tool_id != "rollback_patch":
            return self._deny(request, risk, *(f"BUDGET_EXCEEDED:{name}" for name in exceeded))
        return PolicyDecision("ALLOW", risk, request.request_sha256, approval_id=approval_id,
                              budget_delta=tuple(sorted(delta.items())))

    def authorize_and_reserve(self, request: ToolRequest, snapshot: ExecutionSnapshot) -> PolicyDecision:
        authorization_at = self.clock()
        trusted_request = ToolRequest(
            request.tool_id, request.arguments, request.requested_at,
            authorization_at=authorization_at,
        )
        decision = replace(
            self.evaluate(trusted_request, snapshot),
            authorization_at=authorization_at,
        )
        if decision.status != "ALLOW":
            return decision
        if snapshot.policy_sha256 is None:
            snapshot.policy_sha256 = self.policy.digest
        if request.tool_id != "rollback_patch":
            for name, amount in decision.budget_delta:
                snapshot.budget_usage[name] = snapshot.budget_usage.get(name, 0) + amount
        if request.tool_id == "apply_patch":
            intervention_id = request.arguments["intervention_id"]
            if intervention_id not in snapshot.rollback_obligations:
                snapshot.rollback_obligations.append(intervention_id)
        return decision

    @staticmethod
    def complete_rollback(snapshot: ExecutionSnapshot, intervention_id: str | None = None) -> None:
        if intervention_id is None:
            snapshot.rollback_obligations.clear()
        elif intervention_id in snapshot.rollback_obligations:
            snapshot.rollback_obligations.remove(intervention_id)
