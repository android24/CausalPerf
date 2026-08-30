from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from causalperf_reference.artifacts import digest


_UNSAFE_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9-]", "-", value.upper()).strip("-")
    if not normalized:
        raise ValueError("identifier cannot be normalized")
    return normalized


@dataclass(frozen=True)
class JUnitCounts:
    test_count: int
    failure_count: int
    skipped_count: int


@dataclass(frozen=True)
class CorrectnessEvidenceRequest:
    run_id: str
    phase: str
    suite_id: str
    suite_sha256: str
    source_manifest_id: str
    source_sha256: str
    apk_sha256: str
    command: Mapping[str, object]
    started_at: str
    completed_at: str
    exit_code: int
    result_documents: Mapping[str, bytes] = field(repr=False)
    process_timed_out: bool = False
    process_output_truncated: bool = False

    def __post_init__(self) -> None:
        if self.phase not in {"BASELINE", "TREATMENT"}:
            raise ValueError(f"unsupported correctness phase: {self.phase}")
        if not self.suite_id:
            raise ValueError("suite_id cannot be empty")
        for label, value in (
            ("suite_sha256", self.suite_sha256),
            ("source_sha256", self.source_sha256),
            ("apk_sha256", self.apk_sha256),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"invalid {label}")
        if not re.fullmatch(r"SM-[A-Z0-9-]+", self.source_manifest_id):
            raise ValueError("invalid source manifest ID")
        documents = {
            str(name): bytes(value) for name, value in self.result_documents.items()
        }
        if any(not name for name in documents):
            raise ValueError("JUnit result document names cannot be empty")
        command = dict(self.command)
        if "args" in command:
            command["args"] = tuple(command["args"])
        if "environment" in command:
            command["environment"] = MappingProxyType(dict(command["environment"]))
        object.__setattr__(self, "command", MappingProxyType(command))
        object.__setattr__(self, "result_documents", MappingProxyType(documents))


@dataclass(frozen=True)
class CorrectnessAttempt:
    request: CorrectnessEvidenceRequest
    report: dict
    status: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError(f"invalid correctness status: {self.status}")


def parse_junit_documents(documents: Mapping[str, bytes]) -> JUnitCounts:
    tests = failures = skipped = 0
    for name, payload in sorted(documents.items()):
        if _UNSAFE_XML.search(payload):
            raise ValueError(f"unsafe XML declaration in JUnit result: {name}")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise ValueError(f"malformed JUnit XML {name}: {error}") from error
        if root.tag.rsplit("}", 1)[-1] not in {"testsuite", "testsuites"}:
            raise ValueError(f"unsupported JUnit root in {name}: {root.tag}")
        cases = [
            element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "testcase"
        ]
        for case in cases:
            child_tags = {child.tag.rsplit("}", 1)[-1] for child in case}
            failed = bool(child_tags & {"failure", "error"})
            was_skipped = "skipped" in child_tags
            if failed and was_skipped:
                raise ValueError(f"JUnit testcase is both failed and skipped in {name}")
            tests += 1
            failures += int(failed)
            skipped += int(was_skipped)
    return JUnitCounts(tests, failures, skipped)


def result_artifact_digest(documents: Mapping[str, bytes]) -> str:
    manifest = [
        {
            "name": name,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, payload in sorted(documents.items())
    ]
    return digest(manifest)


class CorrectnessReportParser:
    """Turn raw runner facts into a sealed report and computed gate outcome."""

    def evaluate(self, request: CorrectnessEvidenceRequest) -> CorrectnessAttempt:
        reason_codes: list[str] = []
        try:
            counts = parse_junit_documents(request.result_documents)
        except ValueError:
            counts = JUnitCounts(0, 0, 0)
            reason_codes.append("CORRECTNESS_RESULT_INVALID")

        artifact_sha256 = result_artifact_digest(request.result_documents)
        report = {
            "schema_version": 1,
            "id": f"CR-{_identifier(f'{request.run_id}-{request.phase}-{request.suite_id}')}",
            "run_id": request.run_id,
            "phase": request.phase,
            "started_at": request.started_at,
            "completed_at": request.completed_at,
            "suite_id": request.suite_id,
            "suite_sha256": request.suite_sha256,
            "source_manifest_id": request.source_manifest_id,
            "command_request_sha256": digest(
                {
                    key: dict(value) if isinstance(value, Mapping) else value
                    for key, value in request.command.items()
                }
            ),
            "exit_code": request.exit_code,
            "test_count": counts.test_count,
            "failure_count": counts.failure_count,
            "skipped_count": counts.skipped_count,
            "result_artifact_sha256": artifact_sha256,
        }
        report["content_sha256"] = digest(report, omit=("content_sha256",))

        if request.process_timed_out:
            reason_codes.append("CORRECTNESS_TIMEOUT")
        if request.process_output_truncated:
            reason_codes.append("CORRECTNESS_OUTPUT_LIMIT_EXCEEDED")
        if counts.failure_count:
            reason_codes.append("CORRECTNESS_ASSERTION_FAILED")
        if request.exit_code != 0 and not counts.failure_count:
            reason_codes.append("CORRECTNESS_PROCESS_FAILED")
        if counts.test_count == 0 and "CORRECTNESS_RESULT_INVALID" not in reason_codes:
            reason_codes.append("CORRECTNESS_ZERO_TESTS")

        if counts.failure_count:
            status = "FAIL"
        elif reason_codes:
            status = "INCONCLUSIVE"
        else:
            status = "PASS"
        return CorrectnessAttempt(
            request, report, status, tuple(dict.fromkeys(reason_codes))
        )
