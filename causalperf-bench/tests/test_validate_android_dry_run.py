from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_android_dry_run", ROOT / "tools" / "validate_android_dry_run.py"
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def sha(character: str) -> str:
    return character * 64


def passing_document() -> dict:
    baseline_source = sha("a")
    apk = sha("b")
    value = {
        "schema_version": 1,
        "id": "ADR-CPU-001-BASELINE-001",
        "task_id": "startup-main-thread-cpu-001",
        "task_version": "0.1.0",
        "run_id": "RUN-CPU-001-DRY-001",
        "partition": "DEVELOPMENT",
        "started_at": "2026-08-27T00:00:00Z",
        "completed_at": "2026-08-27T00:10:00Z",
        "task_package_sha256": sha("c"),
        "toolchain_lock_sha256": sha("d"),
        "static_validation": {
            "command_sha256": sha("e"),
            "exit_code": 0,
            "result_artifact_sha256": sha("f"),
        },
        "preflight": {
            "status": "PASS",
            "reason_codes": [],
            "environment_snapshot_sha256": sha("1"),
            "result_artifact_sha256": sha("d"),
        },
        "builds": {
            "baseline": {
                "execution_status": "EXECUTED",
                "command_sha256": sha("2"),
                "source_sha256": baseline_source,
                "clean": True,
                "exit_code": 0,
                "apk_sha256": apk,
                "result_artifact_sha256": sha("3"),
            },
            "restored": {
                "execution_status": "EXECUTED",
                "command_sha256": sha("2"),
                "source_sha256": baseline_source,
                "clean": True,
                "exit_code": 0,
                "apk_sha256": apk,
                "result_artifact_sha256": sha("4"),
            },
        },
        "correctness": {
            "public": {
                "execution_status": "EXECUTED",
                "suite_id": "cpu-001-public-correctness-v1",
                "suite_sha256": sha("5"),
                "command_sha256": sha("6"),
                "source_sha256": baseline_source,
                "apk_sha256": apk,
                "exit_code": 0,
                "test_count": 1,
                "failure_count": 0,
                "skipped_count": 0,
                "result_artifact_sha256": sha("7"),
            },
            "hidden": {
                "execution_status": "EXECUTED",
                "suite_id": "cpu-001-hidden-correctness-v1",
                "suite_sha256": sha("8"),
                "command_sha256": sha("9"),
                "source_sha256": baseline_source,
                "apk_sha256": apk,
                "exit_code": 0,
                "test_count": 2,
                "failure_count": 0,
                "skipped_count": 0,
                "result_artifact_sha256": sha("0"),
            },
        },
        "status": "PASS",
        "reason_codes": [],
        "artifact_digests": [],
        "content_sha256": "",
    }
    value["artifact_digests"] = sorted(validator.referenced_digests(value))
    value["content_sha256"] = validator.canonical_digest(value)
    return value


def reseal(value: dict) -> dict:
    value["artifact_digests"] = sorted(validator.referenced_digests(value))
    value["content_sha256"] = validator.canonical_digest(value)
    return value


class AndroidDryRunValidatorTest(unittest.TestCase):
    def test_complete_dry_run_passes(self):
        result = validator.validate(passing_document())
        self.assertEqual(result["status"], "PASS")

    def test_caller_cannot_self_assert_pass_after_hidden_failure(self):
        value = passing_document()
        value["correctness"]["hidden"]["failure_count"] = 1
        reseal(value)
        with self.assertRaisesRegex(validator.AndroidDryRunError, "computed outcome is FAIL"):
            validator.validate(value)

    def test_zero_hidden_tests_is_computed_inconclusive(self):
        value = passing_document()
        value["correctness"]["hidden"]["test_count"] = 0
        value["status"] = "INCONCLUSIVE"
        value["reason_codes"] = ["HIDDEN_CORRECTNESS_INCONCLUSIVE"]
        reseal(value)
        result = validator.validate(value)
        self.assertEqual(result["status"], "INCONCLUSIVE")

    def test_preflight_failure_can_honestly_record_unrun_steps(self):
        value = passing_document()
        value["preflight"] = {
            "status": "INCONCLUSIVE",
            "reason_codes": ["ANDROID_PLATFORM_MISSING"],
            "result_artifact_sha256": sha("d"),
        }
        for build in value["builds"].values():
            build["execution_status"] = "NOT_RUN"
            build["reason_code"] = "PRECONDITION_FAILED"
            for field in ("exit_code", "apk_sha256", "result_artifact_sha256"):
                build.pop(field, None)
        for report in value["correctness"].values():
            report["execution_status"] = "NOT_RUN"
            report["reason_code"] = "BASELINE_BUILD_NOT_AVAILABLE"
            for field in (
                "apk_sha256", "exit_code", "test_count", "failure_count",
                "skipped_count", "result_artifact_sha256",
            ):
                report.pop(field, None)
        value["status"] = "INCONCLUSIVE"
        value["reason_codes"] = [
            "BASELINE_BUILD_FAILED",
            "HIDDEN_CORRECTNESS_INCONCLUSIVE",
            "PREFLIGHT_INCONCLUSIVE",
            "PUBLIC_CORRECTNESS_INCONCLUSIVE",
            "RESTORED_BUILD_FAILED",
        ]
        reseal(value)
        result = validator.validate(value)
        self.assertEqual(result["status"], "INCONCLUSIVE")

    def test_restored_apk_mismatch_is_computed_fail(self):
        value = passing_document()
        value["builds"]["restored"]["apk_sha256"] = sha("a")
        value["status"] = "FAIL"
        value["reason_codes"] = ["APK_REPRODUCIBILITY_MISMATCH"]
        reseal(value)
        result = validator.validate(value)
        self.assertEqual(result["reason_codes"], ["APK_REPRODUCIBILITY_MISMATCH"])

    def test_correctness_must_bind_to_built_source_and_apk(self):
        value = passing_document()
        value["correctness"]["public"]["source_sha256"] = sha("f")
        reseal(value)
        with self.assertRaisesRegex(validator.AndroidDryRunError, "another source"):
            validator.validate(value)

    def test_artifact_registry_must_be_exact(self):
        value = passing_document()
        value["artifact_digests"].remove(sha("0"))
        value["content_sha256"] = validator.canonical_digest(value)
        with self.assertRaisesRegex(validator.AndroidDryRunError, "exactly bind"):
            validator.validate(value)

    def test_content_tampering_is_rejected(self):
        value = passing_document()
        value["run_id"] = "RUN-TAMPERED"
        with self.assertRaisesRegex(validator.AndroidDryRunError, "content_sha256 mismatch"):
            validator.validate(value)

    def test_correctness_counts_must_be_consistent(self):
        value = passing_document()
        value["correctness"]["hidden"]["failure_count"] = 3
        reseal(value)
        with self.assertRaisesRegex(validator.AndroidDryRunError, "counts are inconsistent"):
            validator.validate(value)


if __name__ == "__main__":
    unittest.main()
