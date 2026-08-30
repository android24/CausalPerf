from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from causalperf_agent.android import CorrectnessEvidenceRequest, CorrectnessReportParser
from causalperf_reference.artifacts import digest, verify_content_digest


ROOT = Path(__file__).parents[2]
SCHEMA = json.loads(
    (ROOT / "shared" / "schemas" / "correctness-report.schema.json").read_text()
)


def suite(*cases: str) -> bytes:
    return ("<testsuite>" + "".join(cases) + "</testsuite>").encode()


PASS_CASE = '<testcase classname="Example" name="passes"/>'
FAIL_CASE = '<testcase classname="Example" name="fails"><failure>no</failure></testcase>'
ERROR_CASE = '<testcase classname="Example" name="errors"><error>boom</error></testcase>'
SKIP_CASE = '<testcase classname="Example" name="skips"><skipped/></testcase>'


class CorrectnessReportParserTest(unittest.TestCase):
    def request(self, documents=None, **overrides):
        value = {
            "run_id": "RUN-CPU-001-DRY-001",
            "phase": "BASELINE",
            "suite_id": "cpu-001-public-correctness-v1",
            "suite_sha256": "a" * 64,
            "source_manifest_id": "SM-CPU-001-BASELINE",
            "source_sha256": "b" * 64,
            "apk_sha256": "c" * 64,
            "command": {
                "executable": "./gradlew",
                "args": [":app:connectedBenchmarkAndroidTest"],
                "working_directory": ".",
                "timeout_seconds": 1800,
            },
            "started_at": "2026-08-29T00:00:00Z",
            "completed_at": "2026-08-29T00:00:02Z",
            "exit_code": 0,
            "result_documents": (
                {"TEST-public.xml": suite(PASS_CASE)} if documents is None else documents
            ),
        }
        value.update(overrides)
        return CorrectnessEvidenceRequest(**value)

    def test_pass_is_computed_from_raw_junit_facts(self):
        attempt = CorrectnessReportParser().evaluate(self.request())
        self.assertEqual(attempt.status, "PASS")
        self.assertEqual(attempt.report["test_count"], 1)
        jsonschema.Draft202012Validator(SCHEMA).validate(attempt.report)
        verify_content_digest(attempt.report)
        self.assertEqual(
            attempt.report["command_request_sha256"], digest(dict(attempt.request.command))
        )

    def test_failures_and_errors_are_both_failures(self):
        attempt = CorrectnessReportParser().evaluate(
            self.request({"TEST-results.xml": suite(PASS_CASE, FAIL_CASE, ERROR_CASE, SKIP_CASE)})
        )
        self.assertEqual(attempt.status, "FAIL")
        self.assertEqual(attempt.report["test_count"], 4)
        self.assertEqual(attempt.report["failure_count"], 2)
        self.assertEqual(attempt.report["skipped_count"], 1)

    def test_multiple_documents_are_aggregated(self):
        attempt = CorrectnessReportParser().evaluate(
            self.request({"a.xml": suite(PASS_CASE), "b.xml": suite(PASS_CASE, SKIP_CASE)})
        )
        self.assertEqual(attempt.report["test_count"], 3)
        self.assertEqual(attempt.report["skipped_count"], 1)

    def test_malformed_or_unsafe_xml_is_inconclusive_not_pass(self):
        documents = (
            {"broken.xml": b"<testsuite>"},
            {"external.xml": b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><testsuite/>'},
        )
        for value in documents:
            with self.subTest(value=value):
                attempt = CorrectnessReportParser().evaluate(self.request(value))
                self.assertEqual(attempt.status, "INCONCLUSIVE")
                self.assertIn("CORRECTNESS_RESULT_INVALID", attempt.reason_codes)
                self.assertEqual(attempt.report["test_count"], 0)

    def test_zero_tests_and_nonzero_process_are_inconclusive(self):
        attempt = CorrectnessReportParser().evaluate(
            self.request({"empty.xml": b"<testsuite/>"}, exit_code=1)
        )
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("CORRECTNESS_PROCESS_FAILED", attempt.reason_codes)
        self.assertIn("CORRECTNESS_ZERO_TESTS", attempt.reason_codes)

    def test_missing_result_documents_are_recorded_as_zero_tests(self):
        attempt = CorrectnessReportParser().evaluate(self.request({}))
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertEqual(attempt.report["test_count"], 0)
        self.assertIn("CORRECTNESS_ZERO_TESTS", attempt.reason_codes)

    def test_timeout_cannot_pass_even_when_xml_contains_a_pass(self):
        attempt = CorrectnessReportParser().evaluate(
            self.request(process_timed_out=True, exit_code=-1)
        )
        self.assertEqual(attempt.status, "INCONCLUSIVE")
        self.assertIn("CORRECTNESS_TIMEOUT", attempt.reason_codes)

    def test_request_seals_documents_and_command(self):
        command = {"executable": "./gradlew", "args": ["connectedCheck"]}
        documents = {"result.xml": suite(PASS_CASE)}
        request = self.request(documents, command=command)
        command["executable"] = "changed"
        command["args"].append("injected")
        documents["result.xml"] = suite(FAIL_CASE)
        self.assertEqual(request.command["executable"], "./gradlew")
        self.assertEqual(request.command["args"], ("connectedCheck",))
        self.assertEqual(request.result_documents["result.xml"], suite(PASS_CASE))


if __name__ == "__main__":
    unittest.main()
