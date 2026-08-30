from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import jsonschema


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "execute_android_dry_run", ROOT / "tools" / "execute_android_dry_run.py"
)
assert SPEC and SPEC.loader
executor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = executor
SPEC.loader.exec_module(executor)
SCHEMA = json.loads((ROOT / "schemas/android-dry-run-result.schema.json").read_text())


def sha(character: str) -> str:
    return character * 64


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        result = f"2026-08-30T02:00:{self.value:02d}Z"
        self.value += 1
        return result


class EvaluatorDryRunCoordinatorTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.public = self.root / "public"
        self.hidden = self.root / "hidden"
        self.workspace = self.root / "evaluator/workspace"
        self.public.mkdir()
        self.hidden.mkdir()
        self.source_sha = sha("d")
        self.apk_sha = sha("e")
        baseline = self.build_attempt(self.public, "BASELINE", sha("2"))
        public_correctness = self.correctness_attempt(
            self.public, "public-suite", sha("5"), sha("f")
        )
        cleanup = SimpleNamespace(status="PASS", result={"content_sha256": sha("a")})
        self.public_execution = SimpleNamespace(
            status="PASS", build=baseline, correctness=public_correctness,
            post_cleanup=cleanup,
        )
        self.cleanup_calls = []

    def build_attempt(self, root, role, result_sha):
        return SimpleNamespace(
            status="PASS",
            request=SimpleNamespace(
                run_id="RUN-CPU-001-DRY-001", role=role, task_root=root.resolve(),
                args=("clean", ":app:assembleBenchmark"),
                source_relative_path="app/src/main",
            ),
            source_sha256=self.source_sha,
            build_result={"command_request_sha256": sha("1"), "content_sha256": result_sha},
            apk_artifact={"sha256": self.apk_sha},
            returncode=0,
        )

    def correctness_attempt(self, root, suite_id, suite_sha, result_sha, failures=0):
        request = SimpleNamespace(
            run_id="RUN-CPU-001-DRY-001", task_root=root.resolve(),
            suite_id=suite_id, suite_sha256=suite_sha,
            source_sha256=self.source_sha, apk_sha256=self.apk_sha,
        )
        return SimpleNamespace(
            status="FAIL" if failures else "PASS", request=request,
            report={
                "suite_id": suite_id, "suite_sha256": suite_sha,
                "command_request_sha256": sha("4"), "exit_code": 0,
                "test_count": 2, "failure_count": failures, "skipped_count": 0,
                "result_artifact_sha256": result_sha,
            },
        )

    def materialize(self, hidden, public, destination):
        self.assertEqual((hidden, public, destination), (self.hidden, self.public, self.workspace))
        destination.mkdir(parents=True)
        return {
            "suite_id": "hidden-suite", "suite_sha256": sha("6"),
            "public_task_sha256": sha("7"), "hidden_package_sha256": sha("8"),
            "workspace_sha256": sha("9"), "overlay_file_count": 1,
            "destination": str(destination),
        }

    def plan(self, *, hidden_factory=None, cleanup=None):
        def default_hidden(workspace, materialization, baseline):
            return self.correctness_attempt(workspace, "hidden-suite", sha("6"), sha("0"))

        def restored(workspace, materialization, baseline):
            return self.build_attempt(workspace, "RESTORED", sha("3"))

        def default_cleanup(workspace):
            self.cleanup_calls.append(workspace)
            return SimpleNamespace(status="PASS", result={"content_sha256": sha("b")})

        return executor.EvaluatorDryRunPlan(
            result_id="ADR-CPU-001-BASELINE-001",
            task_id="startup-main-thread-cpu-001", task_version="0.1.0",
            run_id="RUN-CPU-001-DRY-001", public_root=self.public,
            hidden_root=self.hidden, evaluator_workspace=self.workspace,
            task_package_sha256=sha("7"), toolchain_lock_sha256=sha("8"),
            static_validation={"command_sha256": sha("9"), "exit_code": 0,
                               "result_artifact_sha256": sha("a")},
            preflight={"status": "PASS", "reason_codes": [],
                       "environment_snapshot_sha256": sha("b"),
                       "result_artifact_sha256": sha("c")},
            public_execution=self.public_execution,
            hidden_correctness=hidden_factory or default_hidden,
            restored_build=restored, cleanup=cleanup or default_cleanup,
        )

    def test_closes_hidden_and_restored_gates_into_computed_result(self):
        execution = executor.EvaluatorDryRunCoordinator(
            clock=Clock(), materialize=self.materialize
        ).run(self.plan())
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(self.cleanup_calls, [self.workspace.resolve()])
        jsonschema.Draft202012Validator(SCHEMA).validate(execution.result)

    def test_hidden_failure_is_computed_not_caller_selected(self):
        def failing(workspace, materialization, baseline):
            return self.correctness_attempt(
                workspace, "hidden-suite", sha("6"), sha("0"), failures=1
            )

        execution = executor.EvaluatorDryRunCoordinator(
            clock=Clock(), materialize=self.materialize
        ).run(self.plan(hidden_factory=failing))
        self.assertEqual(execution.status, "FAIL")
        self.assertEqual(execution.result["reason_codes"], ["HIDDEN_CORRECTNESS_FAILED"])

    def test_cleanup_runs_when_hidden_runner_raises(self):
        def crash(workspace, materialization, baseline):
            raise RuntimeError("transport crashed")

        with self.assertRaisesRegex(RuntimeError, "transport crashed"):
            executor.EvaluatorDryRunCoordinator(
                clock=Clock(), materialize=self.materialize
            ).run(self.plan(hidden_factory=crash))
        self.assertEqual(self.cleanup_calls, [self.workspace.resolve()])

    def test_cleanup_failure_prevents_a_pass_artifact(self):
        def cleanup(workspace):
            return SimpleNamespace(status="INCONCLUSIVE", result={"content_sha256": sha("b")})

        with self.assertRaisesRegex(executor.EvaluatorDryRunError, "cleanup did not pass"):
            executor.EvaluatorDryRunCoordinator(
                clock=Clock(), materialize=self.materialize
            ).run(self.plan(cleanup=cleanup))

    def test_hidden_suite_digest_cannot_be_substituted(self):
        def substituted(workspace, materialization, baseline):
            return self.correctness_attempt(workspace, "hidden-suite", sha("1"), sha("0"))

        with self.assertRaisesRegex(executor.EvaluatorDryRunError, "digest is not evaluator-sealed"):
            executor.EvaluatorDryRunCoordinator(
                clock=Clock(), materialize=self.materialize
            ).run(self.plan(hidden_factory=substituted))

    def test_public_failure_stops_before_private_materialization(self):
        self.public_execution.status = "INCONCLUSIVE"
        called = []
        with self.assertRaisesRegex(executor.EvaluatorDryRunError, "public dry run did not pass"):
            executor.EvaluatorDryRunCoordinator(
                clock=Clock(), materialize=lambda *args: called.append(args)
            ).run(self.plan())
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
