from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import jsonschema


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_android_dry_run", ROOT / "tools" / "build_android_dry_run.py"
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
SCHEMA = json.loads((ROOT / "schemas" / "android-dry-run-result.schema.json").read_text())


def sha(character: str) -> str:
    return character * 64


class AndroidDryRunBuilderTest(unittest.TestCase):
    def build_attempt(self, role="BASELINE"):
        return SimpleNamespace(
            request=SimpleNamespace(args=("clean", ":app:assembleBenchmark")),
            source_sha256=sha("d"),
            build_result={
                "command_request_sha256": sha("1"),
                "content_sha256": sha("2") if role == "BASELINE" else sha("3"),
            },
            apk_artifact={"sha256": sha("e")},
            returncode=0,
        )

    def correctness_attempt(self, suite_id: str, suite_sha: str, source_sha: str, apk_sha: str):
        return SimpleNamespace(
            request=SimpleNamespace(source_sha256=source_sha, apk_sha256=apk_sha),
            report={
                "suite_id": suite_id,
                "suite_sha256": suite_sha,
                "command_request_sha256": sha("4"),
                "exit_code": 0,
                "test_count": 1,
                "failure_count": 0,
                "skipped_count": 0,
                "result_artifact_sha256": sha("f") if "public" in suite_id else sha("0"),
            },
        )

    def compose(self, hidden_failure=False):
        baseline = self.build_attempt("BASELINE")
        restored = self.build_attempt("RESTORED")
        source_sha = baseline.source_sha256
        apk_sha = baseline.apk_artifact["sha256"]
        public = self.correctness_attempt("cpu-001-public-correctness-v1", sha("5"), source_sha, apk_sha)
        hidden = self.correctness_attempt("cpu-001-hidden-correctness-v1", sha("6"), source_sha, apk_sha)
        if hidden_failure:
            hidden.report["failure_count"] = 1
        return builder.build_android_dry_run(
            result_id="ADR-CPU-001-BASELINE-001", task_id="startup-main-thread-cpu-001",
            task_version="0.1.0", run_id="RUN-CPU-001-DRY-001",
            started_at="2026-08-29T00:00:00Z", completed_at="2026-08-29T00:01:00Z",
            task_package_sha256=sha("7"), toolchain_lock_sha256=sha("8"),
            static_validation={"command_sha256": sha("9"), "exit_code": 0,
                               "result_artifact_sha256": sha("a")},
            preflight={"status": "PASS", "reason_codes": [],
                       "environment_snapshot_sha256": sha("b"),
                       "result_artifact_sha256": sha("c")},
            baseline_build=builder.executed_build(baseline),
            restored_build=builder.executed_build(restored),
            public_correctness=builder.executed_correctness(public),
            hidden_correctness=builder.executed_correctness(hidden),
        )

    def test_composes_adapter_shaped_facts_into_schema_valid_pass(self):
        result = self.compose()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reason_codes"], [])
        jsonschema.Draft202012Validator(SCHEMA).validate(result)

    def test_hidden_assertion_failure_is_computed_fail(self):
        result = self.compose(hidden_failure=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason_codes"], ["HIDDEN_CORRECTNESS_FAILED"])

    def test_sdk_missing_path_is_honest_inconclusive(self):
        source = sha("d")
        result = builder.build_android_dry_run(
            result_id="ADR-CPU-001-NO-SDK-001", task_id="startup-main-thread-cpu-001",
            task_version="0.1.0", run_id="RUN-CPU-001-DRY-002",
            started_at="2026-08-29T00:00:00Z", completed_at="2026-08-29T00:00:01Z",
            task_package_sha256=sha("7"), toolchain_lock_sha256=sha("8"),
            static_validation={"command_sha256": sha("9"), "exit_code": 0,
                               "result_artifact_sha256": sha("a")},
            preflight={"status": "INCONCLUSIVE", "reason_codes": ["ANDROID_SDK_MISSING"],
                       "result_artifact_sha256": sha("c")},
            baseline_build=builder.unrun_build(command_sha256=sha("1"), source_sha256=source,
                                               clean=True, reason="PRECONDITION_FAILED"),
            restored_build=builder.unrun_build(command_sha256=sha("1"), source_sha256=source,
                                               clean=True, reason="PRECONDITION_FAILED"),
            public_correctness=builder.unrun_correctness(
                suite_id="public", suite_sha256=sha("2"), command_sha256=sha("3"),
                source_sha256=source, reason="BASELINE_BUILD_NOT_AVAILABLE"),
            hidden_correctness=builder.unrun_correctness(
                suite_id="hidden", suite_sha256=sha("4"), command_sha256=sha("3"),
                source_sha256=source, reason="BASELINE_BUILD_NOT_AVAILABLE"),
        )
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertNotIn("apk_sha256", result["builds"]["baseline"])


if __name__ == "__main__":
    unittest.main()
