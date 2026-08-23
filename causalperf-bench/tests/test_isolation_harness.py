from __future__ import annotations

import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

from isolation import BackendUnavailable, CommandSpec, IsolationHarness, IsolationPolicy, IsolationRunSpec, PrivateCanarySet
from isolation.backends import (
    DarwinSandboxBackend,
    LinuxBubblewrapBackend,
    ProcessOutcome,
    WindowsSandboxBackend,
)
from isolation.model import IsolationContractError, canonical_digest


CANARY_ONE = "CAUSALPERF_PRIVATE_CANARY_0123456789ABCDEF"
CANARY_TWO = "CAUSALPERF_PRIVATE_CANARY_FEDCBA9876543210"


def sealed(value):
    value["content_sha256"] = canonical_digest(value, omit=("content_sha256",))
    return value


def policy_document(**changes):
    value = {
        "schema_version": 2,
        "id": "ISO-RUN-1",
        "run_id": "RUN-1",
        "backend": "AUTO",
        "network": "denied",
        "allowed_executables": [str(Path(sys.executable).resolve())],
        "runtime_read_paths": [str(Path(sys.prefix).resolve())],
        "host_denied_read_paths": ["/Users", "/tmp", "/private/tmp", "/private/var/folders"],
        "agent_environment_keys": ["LANG"],
        "evaluator_environment_keys": ["LANG"],
        "writable_paths": ["app/src/main"],
        "protected_paths": ["tests", "task.yaml"],
        "limits": {
            "wall_time_seconds": 30,
            "output_bytes": 100000,
            "scan_files": 100,
            "scan_bytes": 1000000,
        },
    }
    value.update(changes)
    return sealed(value)


def canary_document():
    return sealed({
        "schema_version": 1,
        "id": "CS-TASK-ONE",
        "task_id": "task-one",
        "task_version": "0.1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "canaries": [
            {"id": "CANARY-ONE", "value": CANARY_ONE},
            {"id": "CANARY-TWO", "value": CANARY_TWO},
        ],
    })


def run_document(spec, policy, canaries):
    return sealed({
        "schema_version": 2,
        "id": "ISR-RUN-ONE",
        "run_id": policy.run_id,
        "task_id": spec.task_id,
        "task_version": spec.task_version,
        "policy_sha256": policy.digest,
        "canary_set_sha256": canaries.digest,
        "public_source": str(spec.public_source.resolve()),
        "private_evaluator": str(spec.private_evaluator.resolve()),
        "run_root": str(spec.run_root.resolve()),
        "agent_command": {
            "executable": spec.agent_command.executable,
            "args": list(spec.agent_command.args),
            "working_directory": str(spec.agent_command.working_directory.resolve()),
            "environment": spec.agent_command.environment,
        },
        "evaluator_command": {
            "executable": spec.evaluator_command.executable,
            "args": list(spec.evaluator_command.args),
            "working_directory": str(spec.evaluator_command.working_directory.resolve()),
            "environment": spec.evaluator_command.environment,
        },
    })


class FakeBackend:
    name = "LINUX_BWRAP"
    network_denied = True
    separate_views = True
    owned_process_group = True

    def __init__(self, mode="pass"):
        self.mode = mode
        self.calls = []

    def run(self, command, *, read_roots, write_roots, policy, stdout_path, stderr_path):
        self.calls.append({"read_roots": read_roots, "write_roots": write_roots})
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = b"ok"
        stderr = b""
        if len(self.calls) == 1:
            workspace = read_roots[0]
            (workspace / "app" / "src" / "main" / "change.txt").write_text("safe", encoding="utf-8")
            if self.mode == "agent_output_leak":
                (write_roots[-1] / "result.txt").write_text(CANARY_ONE, encoding="utf-8")
            if self.mode == "agent_log_leak":
                stdout = CANARY_ONE.encode()
            if self.mode == "protected_mutation":
                protected = workspace / "task.yaml"
                protected.chmod(protected.stat().st_mode | stat.S_IWUSR)
                protected.write_text("changed", encoding="utf-8")
            exit_code = 1 if self.mode == "agent_failure" else 0
            timed_out = self.mode == "agent_timeout"
        else:
            if self.mode == "evaluator_output_leak":
                (write_roots[0] / "public-result.txt").write_text(CANARY_TWO, encoding="utf-8")
            else:
                (write_roots[0] / "public-result.txt").write_text("ACCEPT", encoding="utf-8")
            exit_code = 1 if self.mode == "evaluator_failure" else 0
            timed_out = self.mode == "evaluator_timeout"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        return ProcessOutcome(
            exit_code=exit_code,
            timed_out=timed_out,
            output_limit_exceeded=self.mode == "output_limit",
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
        )


class IsolationHarnessTest(unittest.TestCase):
    def make_spec(self, root: Path, *, agent_environment=None, evaluator_environment=None):
        public = root / "public"
        private = root / "private"
        run = root / "run"
        (public / "app" / "src" / "main").mkdir(parents=True)
        (public / "tests").mkdir()
        (public / "app" / "src" / "main" / "Main.kt").write_text("class Main", encoding="utf-8")
        (public / "tests" / "Correctness.kt").write_text("test", encoding="utf-8")
        (public / "task.yaml").write_text("id: task-one", encoding="utf-8")
        private.mkdir()
        (private / "ground-truth.json").write_text("{}", encoding="utf-8")
        executable = str(Path(sys.executable).resolve())
        return IsolationRunSpec(
            task_id="task-one",
            task_version="0.1.0",
            public_source=public,
            private_evaluator=private,
            run_root=run,
            agent_command=CommandSpec(
                executable, (), run / "agent-view" / "workspace",
                agent_environment or {"LANG": "C"},
            ),
            evaluator_command=CommandSpec(
                executable, (), private,
                evaluator_environment or {"LANG": "C"},
            ),
        )

    def harness(self, backend):
        return IsolationHarness(
            IsolationPolicy(policy_document()), PrivateCanarySet(canary_document()),
            backend=backend, clock=lambda: "2026-01-01T00:00:00Z",
        )

    def test_separate_views_and_clean_post_scans_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = FakeBackend()
            spec = self.make_spec(root)
            report = self.harness(backend).run(spec)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(len(backend.calls), 2)
            self.assertNotIn(spec.private_evaluator.resolve(), backend.calls[0]["read_roots"])
            self.assertIn(spec.private_evaluator.resolve(), backend.calls[1]["read_roots"])
            self.assertTrue(all(report["controls"].values()))

    def test_pre_input_canary_stops_before_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = FakeBackend(); spec = self.make_spec(root)
            (spec.public_source / "app" / "src" / "main" / "leak.txt").write_text(CANARY_ONE, encoding="utf-8")
            report = self.harness(backend).run(spec)
            self.assertEqual(report["status"], "LEAK_DETECTED")
            self.assertEqual(backend.calls, [])

    def test_environment_canary_stops_before_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = FakeBackend()
            spec = self.make_spec(root, agent_environment={"LANG": CANARY_ONE})
            report = self.harness(backend).run(spec)
            self.assertEqual(report["status"], "LEAK_DETECTED")
            self.assertEqual(backend.calls, [])

    def test_agent_output_and_log_canaries_are_detected(self):
        for mode in ("agent_output_leak", "agent_log_leak"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); backend = FakeBackend(mode); spec = self.make_spec(root)
                report = self.harness(backend).run(spec)
                self.assertEqual(report["status"], "LEAK_DETECTED")
                self.assertEqual(len(backend.calls), 1)

    def test_evaluator_cannot_publish_canary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = FakeBackend("evaluator_output_leak")
            report = self.harness(backend).run(self.make_spec(root))
            self.assertEqual(report["status"], "LEAK_DETECTED")
            self.assertIn("LEAKAGE_SCAN_FAILED", report["reason_codes"])

    def test_protected_path_change_fails_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.harness(FakeBackend("protected_mutation")).run(self.make_spec(root))
            self.assertEqual(report["status"], "AGENT_FAILED")
            self.assertIn("PROTECTED_PATH_CHANGED", report["reason_codes"])

    def test_process_failures_and_output_limit_are_bounded_codes(self):
        for mode, reason in (
            ("agent_failure", "AGENT_PROCESS_FAILED"),
            ("agent_timeout", "AGENT_PROCESS_TIMEOUT"),
            ("output_limit", "OUTPUT_LIMIT_EXCEEDED"),
            ("evaluator_failure", "EVALUATOR_PROCESS_FAILED"),
            ("evaluator_timeout", "EVALUATOR_PROCESS_TIMEOUT"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report = self.harness(FakeBackend(mode)).run(self.make_spec(root))
                self.assertIn(reason, report["reason_codes"])
                self.assertNotEqual(report["status"], "PASS")

    def test_non_allowlisted_environment_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.make_spec(root, agent_environment={"SECRET_TOKEN": "x"})
            with self.assertRaisesRegex(IsolationContractError, "non-allowlisted"):
                self.harness(FakeBackend()).run(spec)

    def test_unavailable_backend_returns_unsupported_without_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            harness = IsolationHarness(
                IsolationPolicy(policy_document()), PrivateCanarySet(canary_document()),
                clock=lambda: "2026-01-01T00:00:00Z",
            )
            with patch("isolation.harness.select_backend", side_effect=BackendUnavailable("ISOLATION_BACKEND_PROBE_FAILED")):
                report = harness.run(spec)
            self.assertEqual(report["status"], "UNSUPPORTED")
            self.assertEqual(report["reason_codes"], ["ISOLATION_BACKEND_PROBE_FAILED"])
            self.assertFalse(spec.run_root.exists())

    def test_backend_launch_plan_failure_returns_bounded_unsupported_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root); backend = FakeBackend()
            backend.run = MagicMock(
                side_effect=BackendUnavailable("WINDOWS_SANDBOX_PATH_NOT_MAPPED")
            )
            report = self.harness(backend).run(spec)
            self.assertEqual(report["status"], "UNSUPPORTED")
            self.assertEqual(report["reason_codes"], ["ISOLATION_BACKEND_PROBE_FAILED"])

    def test_layout_must_be_physically_disjoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            bad = IsolationRunSpec(
                spec.task_id, spec.task_version, spec.public_source,
                spec.public_source / "private", spec.run_root,
                spec.agent_command, spec.evaluator_command,
            )
            with self.assertRaisesRegex(IsolationContractError, "disjoint"):
                self.harness(FakeBackend()).run(bad)

    def test_broad_runtime_scope_that_contains_private_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            broad = IsolationPolicy(policy_document(runtime_read_paths=[str(root.resolve())]))
            harness = IsolationHarness(broad, PrivateCanarySet(canary_document()), backend=FakeBackend())
            with self.assertRaisesRegex(IsolationContractError, "expose the private"):
                harness.run(spec)

    def test_agent_metadata_cannot_reveal_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            bad_agent = CommandSpec(
                spec.agent_command.executable,
                (str(spec.private_evaluator.resolve()),),
                spec.agent_command.working_directory,
                spec.agent_command.environment,
            )
            bad = IsolationRunSpec(
                spec.task_id, spec.task_version, spec.public_source,
                spec.private_evaluator, spec.run_root, bad_agent,
                spec.evaluator_command,
            )
            with self.assertRaisesRegex(IsolationContractError, "metadata"):
                self.harness(FakeBackend()).run(bad)

    def test_run_spec_binds_exact_policy_and_private_canary_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            policy = IsolationPolicy(policy_document())
            canaries = PrivateCanarySet(canary_document())
            document = run_document(spec, policy, canaries)
            restored = IsolationRunSpec.from_document(document, policy, canaries)
            self.assertEqual(restored.task_id, spec.task_id)
            changed_policy = IsolationPolicy(policy_document(id="ISO-RUN-2"))
            with self.assertRaisesRegex(IsolationContractError, "active policy"):
                IsolationRunSpec.from_document(document, changed_policy, canaries)

    def test_tampered_run_spec_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = self.make_spec(root)
            policy = IsolationPolicy(policy_document())
            canaries = PrivateCanarySet(canary_document())
            document = run_document(spec, policy, canaries)
            document["task_id"] = "task-tampered"
            with self.assertRaisesRegex(IsolationContractError, "digest"):
                IsolationRunSpec.from_document(document, policy, canaries)

    def test_v1_policy_is_validated_and_migrated_to_v2(self):
        document = policy_document()
        document["schema_version"] = 1
        document["content_sha256"] = canonical_digest(document, omit=("content_sha256",))
        policy = IsolationPolicy(document)
        self.assertEqual(policy.get("schema_version"), 2)
        self.assertEqual(policy.source_digest, document["content_sha256"])
        self.assertNotEqual(policy.digest, policy.source_digest)

    def test_windows_drive_paths_are_valid_contract_paths(self):
        policy = IsolationPolicy(policy_document(
            backend="WINDOWS_SANDBOX",
            allowed_executables=[r"C:\Runtime\python.exe"],
            runtime_read_paths=[r"C:\Runtime"],
            host_denied_read_paths=[r"C:\Private"],
        ))
        command = CommandSpec(
            r"C:\Runtime\python.exe", (), Path(r"C:\Work"), {"LANG": "C"}
        )
        command.validate(policy, evaluator=False)


class BackendContractTest(unittest.TestCase):
    def test_darwin_profile_denies_network_and_omits_private_root(self):
        policy = IsolationPolicy(policy_document())
        backend = DarwinSandboxBackend()
        profile = backend._profile(
            read_roots=(Path("/tmp/public-view"),),
            write_roots=(Path("/tmp/agent-output"),),
            policy=policy,
        )
        self.assertIn("(deny network*)", profile)
        self.assertNotIn("private-evaluator", profile)
        self.assertIn("(allow signal (target self))", profile)
        self.assertIn("(deny file-write*)", profile)
        self.assertIn("(deny file-read* (subpath \"/Users\"))", profile)

    def test_linux_command_unshares_network_pid_and_mount_views(self):
        policy = IsolationPolicy(policy_document(backend="LINUX_BWRAP"))
        command = CommandSpec(
            str(Path(sys.executable).resolve()), (), Path("/tmp/public-view"), {"LANG": "C"}
        )
        wrapped = LinuxBubblewrapBackend("/usr/bin/bwrap").wrap(
            command,
            read_roots=(Path("/tmp/public-view"),),
            write_roots=(Path("/tmp/agent-output"),),
            policy=policy,
        )
        self.assertIn("--unshare-all", wrapped)
        self.assertIn("--clearenv", wrapped)
        self.assertNotIn("private-evaluator", " ".join(wrapped))

    def test_windows_configuration_disables_host_integrations(self):
        mapping = __import__(
            "isolation.backends", fromlist=["_WindowsFolderMapping"]
        )._WindowsFolderMapping(
            Path(r"C:\Public"),
            PureWindowsPath(r"C:\CausalPerf\Input\Read0"),
            True,
        )
        xml = WindowsSandboxBackend._configuration_xml([mapping])
        self.assertIn("<Networking>Disable</Networking>", xml)
        self.assertIn("<ClipboardRedirection>Disable</ClipboardRedirection>", xml)
        self.assertIn("<ProtectedClient>Enable</ProtectedClient>", xml)
        self.assertIn("<ReadOnly>true</ReadOnly>", xml)

    def test_windows_launch_uses_local_copy_and_exact_writeback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            writable = workspace / "app" / "src" / "main"
            output = root / "output"
            control = root / "control"
            result = root / "result"
            runtime = root / "runtime"
            executable = runtime / "python.exe"
            for path in (writable, output, control, result, runtime):
                path.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"binary")
            policy = IsolationPolicy(policy_document(
                backend="WINDOWS_SANDBOX",
                allowed_executables=[str(executable.resolve())],
                runtime_read_paths=[str(runtime.resolve())],
            ))
            command = CommandSpec(
                str(executable.resolve()), (str(writable.resolve()),), workspace,
                {"LANG": "C"},
            )
            mappings, specification = WindowsSandboxBackend("wsb.exe")._build_launch(
                command,
                read_roots=(workspace,),
                write_roots=(writable, output),
                policy=policy,
                control=control,
                result=result,
            )
            workspace_mapping = next(item for item in mappings if item.host == workspace.resolve())
            writable_mapping = next(item for item in mappings if item.host == writable.resolve())
            self.assertTrue(workspace_mapping.read_only)
            self.assertFalse(writable_mapping.read_only)
            self.assertIn(r"C:\CausalPerf\Local\Read0", specification["working_directory"])
            self.assertEqual(len(specification["copy_back"]), 1)
            self.assertIn(r"C:\CausalPerf\WriteBack", specification["copy_back"][0]["destination"])
            self.assertNotIn("private-evaluator", json.dumps(specification))

    def test_windows_bootstrap_uses_data_arguments_without_expression_eval(self):
        script = WindowsSandboxBackend._bootstrap_script()
        self.assertIn("& $spec.executable @($spec.args)", script)
        self.assertIn("Get-ChildItem Env:", script)
        self.assertIn("robocopy.exe", script)
        self.assertIn('Set-Item Env:SystemRoot "C:\\Windows"', script)
        self.assertNotIn("Invoke-Expression", script)

    def test_windows_run_starts_and_stops_the_same_owned_sandbox_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            writable = workspace / "app"
            runtime = root / "runtime"
            executable = runtime / "agent.exe"
            output = root / "output"
            logs = root / "logs"
            for path in (writable, runtime, output, logs):
                path.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"binary")
            policy = IsolationPolicy(policy_document(
                backend="WINDOWS_SANDBOX",
                allowed_executables=[str(executable.resolve())],
                runtime_read_paths=[str(runtime.resolve())],
                writable_paths=["app"],
            ))
            backend = WindowsSandboxBackend("wsb.exe")
            original_build = backend._build_launch

            def build_with_status(*args, **kwargs):
                mappings, specification = original_build(*args, **kwargs)
                (kwargs["result"] / "status.json").write_text(
                    '{"exit_code":0}', encoding="utf-8"
                )
                return mappings, specification

            process = MagicMock()
            process.poll.return_value = 0
            process.returncode = 0
            with (
                patch.object(backend, "_build_launch", side_effect=build_with_status),
                patch("isolation.backends.subprocess.Popen", return_value=process) as launch,
                patch("isolation.backends.subprocess.run") as lifecycle,
            ):
                outcome = backend.run(
                    CommandSpec(str(executable.resolve()), (), workspace, {"LANG": "C"}),
                    read_roots=(workspace,),
                    write_roots=(writable, output),
                    policy=policy,
                    stdout_path=logs / "stdout.log",
                    stderr_path=logs / "stderr.log",
                )
            start = launch.call_args.args[0]
            stop = lifecycle.call_args.args[0]
            self.assertEqual(start[:2], ["wsb.exe", "start"])
            self.assertEqual(stop[:2], ["wsb.exe", "stop"])
            self.assertEqual(start[start.index("--id") + 1], stop[stop.index("--id") + 1])
            self.assertEqual(outcome.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
