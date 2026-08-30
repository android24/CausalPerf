#!/usr/bin/env python3
"""Compose raw runner facts into a computed AndroidDryRunResult."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path


_VALIDATOR_PATH = Path(__file__).with_name("validate_android_dry_run.py")
_SPEC = importlib.util.spec_from_file_location("causalperf_android_dry_run_validator", _VALIDATOR_PATH)
assert _SPEC and _SPEC.loader
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)


def executed_build(attempt) -> dict:
    """Normalize a trusted GradleBuildAttempt without accepting a caller status."""
    apk_sha256 = attempt.apk_artifact["sha256"] if attempt.apk_artifact else None
    value = {
        "execution_status": "EXECUTED",
        "command_sha256": attempt.build_result["command_request_sha256"],
        "source_sha256": attempt.source_sha256,
        "clean": bool(attempt.request.args and attempt.request.args[0] == "clean"),
        "exit_code": attempt.returncode if attempt.returncode is not None else -1,
        "result_artifact_sha256": attempt.build_result["content_sha256"],
    }
    if apk_sha256 is not None and value["exit_code"] == 0:
        value["apk_sha256"] = apk_sha256
    return value


def unrun_build(*, command_sha256: str, source_sha256: str, clean: bool, reason: str) -> dict:
    return {
        "execution_status": "NOT_RUN",
        "command_sha256": command_sha256,
        "source_sha256": source_sha256,
        "clean": clean,
        "reason_code": reason,
    }


def executed_correctness(attempt) -> dict:
    """Normalize a sealed CorrectnessAttempt; PASS/FAIL is recomputed downstream."""
    report = attempt.report
    request = attempt.request
    return {
        "execution_status": "EXECUTED",
        "suite_id": report["suite_id"],
        "suite_sha256": report["suite_sha256"],
        "command_sha256": report["command_request_sha256"],
        "source_sha256": request.source_sha256,
        "apk_sha256": request.apk_sha256,
        "exit_code": report["exit_code"],
        "test_count": report["test_count"],
        "failure_count": report["failure_count"],
        "skipped_count": report["skipped_count"],
        "result_artifact_sha256": report["result_artifact_sha256"],
    }


def unrun_correctness(
    *, suite_id: str, suite_sha256: str, command_sha256: str,
    source_sha256: str, reason: str,
) -> dict:
    return {
        "execution_status": "NOT_RUN",
        "suite_id": suite_id,
        "suite_sha256": suite_sha256,
        "command_sha256": command_sha256,
        "source_sha256": source_sha256,
        "reason_code": reason,
    }


def build_android_dry_run(
    *,
    result_id: str,
    task_id: str,
    task_version: str,
    run_id: str,
    started_at: str,
    completed_at: str,
    task_package_sha256: str,
    toolchain_lock_sha256: str,
    static_validation: dict,
    preflight: dict,
    baseline_build: dict,
    restored_build: dict,
    public_correctness: dict,
    hidden_correctness: dict,
) -> dict:
    """Build, compute and validate a DEVELOPMENT dry-run result atomically."""
    document = {
        "schema_version": 1,
        "id": result_id,
        "task_id": task_id,
        "task_version": task_version,
        "run_id": run_id,
        "partition": "DEVELOPMENT",
        "started_at": started_at,
        "completed_at": completed_at,
        "task_package_sha256": task_package_sha256,
        "toolchain_lock_sha256": toolchain_lock_sha256,
        "static_validation": deepcopy(static_validation),
        "preflight": deepcopy(preflight),
        "builds": {
            "baseline": deepcopy(baseline_build),
            "restored": deepcopy(restored_build),
        },
        "correctness": {
            "public": deepcopy(public_correctness),
            "hidden": deepcopy(hidden_correctness),
        },
    }
    status, reasons = _VALIDATOR.expected_outcome(document)
    document["status"] = status
    document["reason_codes"] = reasons
    document["artifact_digests"] = sorted(_VALIDATOR.referenced_digests(document))
    document["content_sha256"] = _VALIDATOR.canonical_digest(document)
    _VALIDATOR.validate(document)
    return document
