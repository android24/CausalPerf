#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import jsonschema


SCHEMA = Path(__file__).parents[1] / "schemas" / "android-dry-run-result.schema.json"


class AndroidDryRunError(ValueError):
    pass


def canonical_digest(document: dict) -> str:
    value = {key: item for key, item in document.items() if key != "content_sha256"}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AndroidDryRunError("timestamp lacks timezone")
    return parsed


def expected_outcome(document: dict) -> tuple[str, list[str]]:
    inconclusive: list[str] = []
    failed: list[str] = []

    if document["static_validation"]["exit_code"] != 0:
        inconclusive.append("STATIC_TOOLCHAIN_INVALID")
    if document["preflight"]["status"] != "PASS":
        inconclusive.append("PREFLIGHT_INCONCLUSIVE")

    for name, label in (("baseline", "BASELINE"), ("restored", "RESTORED")):
        build = document["builds"][name]
        if not build["clean"]:
            failed.append(f"{label}_CLEAN_BUILD_REQUIRED")
        if build["execution_status"] != "EXECUTED" or build["exit_code"] != 0:
            inconclusive.append(f"{label}_BUILD_FAILED")

    baseline = document["builds"]["baseline"]
    restored = document["builds"]["restored"]
    if baseline["source_sha256"] != restored["source_sha256"]:
        failed.append("SOURCE_RESTORATION_MISMATCH")
    if (
        baseline.get("exit_code") == 0
        and restored.get("exit_code") == 0
        and baseline.get("apk_sha256") != restored.get("apk_sha256")
    ):
        failed.append("APK_REPRODUCIBILITY_MISMATCH")

    for name, label in (("public", "PUBLIC"), ("hidden", "HIDDEN")):
        report = document["correctness"][name]
        if report["execution_status"] != "EXECUTED":
            inconclusive.append(f"{label}_CORRECTNESS_INCONCLUSIVE")
        elif report["failure_count"] > 0:
            failed.append(f"{label}_CORRECTNESS_FAILED")
        elif report["exit_code"] != 0 or report["test_count"] == 0:
            inconclusive.append(f"{label}_CORRECTNESS_INCONCLUSIVE")

    reasons = sorted(set(failed + inconclusive))
    if failed:
        return "FAIL", reasons
    if inconclusive:
        return "INCONCLUSIVE", reasons
    return "PASS", []


def referenced_digests(document: dict) -> set[str]:
    result = {
        document["task_package_sha256"],
        document["toolchain_lock_sha256"],
        document["static_validation"]["command_sha256"],
        document["static_validation"]["result_artifact_sha256"],
        document["preflight"]["result_artifact_sha256"],
    }
    if "environment_snapshot_sha256" in document["preflight"]:
        result.add(document["preflight"]["environment_snapshot_sha256"])
    for build in document["builds"].values():
        result.update((build["command_sha256"], build["source_sha256"]))
        if "result_artifact_sha256" in build:
            result.add(build["result_artifact_sha256"])
        if "apk_sha256" in build:
            result.add(build["apk_sha256"])
    for report in document["correctness"].values():
        result.update((report["suite_sha256"], report["command_sha256"], report["source_sha256"]))
        for field in ("apk_sha256", "result_artifact_sha256"):
            if field in report:
                result.add(report[field])
    return result


def validate(document: dict) -> dict:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(document)
    if canonical_digest(document) != document["content_sha256"]:
        raise AndroidDryRunError("content_sha256 mismatch")
    if parse_time(document["started_at"]) > parse_time(document["completed_at"]):
        raise AndroidDryRunError("dry run completes before it starts")

    preflight = document["preflight"]
    if preflight["status"] == "PASS" and preflight["reason_codes"]:
        raise AndroidDryRunError("passing preflight carries reason codes")
    if preflight["status"] == "INCONCLUSIVE" and not preflight["reason_codes"]:
        raise AndroidDryRunError("inconclusive preflight lacks a reason code")

    for name, build in document["builds"].items():
        if build["execution_status"] == "NOT_RUN":
            forbidden = {"exit_code", "apk_sha256", "result_artifact_sha256"} & set(build)
            if forbidden:
                raise AndroidDryRunError(f"{name} NOT_RUN build carries execution results")
        elif "reason_code" in build:
            raise AndroidDryRunError(f"{name} executed build carries a NOT_RUN reason")

    for name, report in document["correctness"].items():
        if report["execution_status"] == "NOT_RUN":
            forbidden = {
                "apk_sha256", "exit_code", "test_count", "failure_count",
                "skipped_count", "result_artifact_sha256",
            } & set(report)
            if forbidden:
                raise AndroidDryRunError(f"{name} NOT_RUN correctness carries execution results")
        elif "reason_code" in report:
            raise AndroidDryRunError(f"{name} executed correctness carries a NOT_RUN reason")
        if (
            report["execution_status"] == "EXECUTED"
            and report["failure_count"] + report["skipped_count"] > report["test_count"]
        ):
            raise AndroidDryRunError(f"{name} correctness counts are inconsistent")
    public = document["correctness"]["public"]
    hidden = document["correctness"]["hidden"]
    if public["suite_id"] == hidden["suite_id"] or public["suite_sha256"] == hidden["suite_sha256"]:
        raise AndroidDryRunError("public and hidden correctness suites are not independent")

    baseline = document["builds"]["baseline"]
    host_gate_passed = (
        document["static_validation"]["exit_code"] == 0
        and preflight["status"] == "PASS"
    )
    if not host_gate_passed and any(
        build["execution_status"] == "EXECUTED"
        for build in document["builds"].values()
    ):
        raise AndroidDryRunError("build executed after static validation or preflight failed")
    baseline_succeeded = (
        baseline["execution_status"] == "EXECUTED" and baseline.get("exit_code") == 0
    )
    if not baseline_succeeded and any(
        report["execution_status"] == "EXECUTED"
        for report in document["correctness"].values()
    ):
        raise AndroidDryRunError("correctness executed without a successful baseline build")
    for name, report in document["correctness"].items():
        if report["source_sha256"] != baseline["source_sha256"]:
            raise AndroidDryRunError(f"{name} correctness is bound to another source")
        if (
            report["execution_status"] == "EXECUTED"
            and report["apk_sha256"] != baseline.get("apk_sha256")
        ):
            raise AndroidDryRunError(f"{name} correctness is bound to another APK")

    expected_digests = referenced_digests(document)
    if set(document["artifact_digests"]) != expected_digests:
        raise AndroidDryRunError("artifact_digests does not exactly bind referenced evidence")
    expected_status, expected_reasons = expected_outcome(document)
    if document["status"] != expected_status or document["reason_codes"] != expected_reasons:
        raise AndroidDryRunError(
            f"computed outcome is {expected_status} {expected_reasons}, "
            f"not {document['status']} {document['reason_codes']}"
        )
    return {"id": document["id"], "status": expected_status, "reason_codes": expected_reasons}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a computed Android dry-run result")
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.result.read_text(encoding="utf-8"))
        result = validate(document)
    except (OSError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as error:
        print(f"FAIL {error}")
        return 1
    print(f"PASS {result['id']}={result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
