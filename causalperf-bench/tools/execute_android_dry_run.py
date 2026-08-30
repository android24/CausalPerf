#!/usr/bin/env python3
"""Evaluator-only orchestration for the private half of an Android dry run.

This module intentionally uses dependency-injected runner callbacks.  It never
imports private inputs into the Agent process and never accepts a caller-picked
dry-run verdict; the existing Bench builder computes the final outcome.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _load(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"causalperf_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUILDER = _load("build_android_dry_run.py")
_MATERIALIZER = _load("materialize_hidden_correctness.py")


class EvaluatorDryRunError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluatorDryRunPlan:
    result_id: str
    task_id: str
    task_version: str
    run_id: str
    public_root: Path
    hidden_root: Path
    evaluator_workspace: Path
    task_package_sha256: str
    toolchain_lock_sha256: str
    static_validation: dict
    preflight: dict
    public_execution: object
    hidden_correctness: Callable[[Path, dict, object], object]
    restored_build: Callable[[Path, dict, object], object]
    cleanup: Callable[[Path], object]


@dataclass(frozen=True)
class EvaluatorDryRunExecution:
    plan: EvaluatorDryRunPlan
    materialization: dict
    hidden_correctness: object
    cleanup: object
    restored_build: object
    result: dict

    @property
    def status(self) -> str:
        return self.result["status"]


class EvaluatorDryRunCoordinator:
    """Close hidden correctness and restored-build gates after Agent exit."""

    def __init__(
        self,
        *,
        clock: Callable[[], str],
        materialize: Callable[[Path, Path, Path], dict] = _MATERIALIZER.materialize,
    ):
        self.clock = clock
        self.materialize = materialize

    @staticmethod
    def _baseline(plan: EvaluatorDryRunPlan):
        public = plan.public_execution
        if getattr(public, "status", None) != "PASS":
            raise EvaluatorDryRunError("public dry run did not pass")
        build = getattr(public, "build", None)
        correctness = getattr(public, "correctness", None)
        cleanup = getattr(public, "post_cleanup", None)
        if build is None or getattr(build, "status", None) != "PASS" or build.apk_artifact is None:
            raise EvaluatorDryRunError("public dry run lacks a passing baseline build")
        if correctness is None or getattr(correctness, "status", None) != "PASS":
            raise EvaluatorDryRunError("public dry run lacks passing public correctness")
        if cleanup is None or getattr(cleanup, "status", None) != "PASS":
            raise EvaluatorDryRunError("public dry run lacks passing post-cleanup")
        if build.request.run_id != plan.run_id:
            raise EvaluatorDryRunError("public baseline belongs to another run")
        return build, correctness

    @staticmethod
    def _validate_hidden(plan, baseline, materialization, attempt) -> None:
        request = attempt.request
        if request.run_id != plan.run_id:
            raise EvaluatorDryRunError("hidden correctness belongs to another run")
        if request.task_root != plan.evaluator_workspace.resolve():
            raise EvaluatorDryRunError("hidden correctness uses another workspace")
        if request.suite_id != materialization["suite_id"]:
            raise EvaluatorDryRunError("hidden correctness suite ID is not evaluator-sealed")
        if request.suite_sha256 != materialization["suite_sha256"]:
            raise EvaluatorDryRunError("hidden correctness suite digest is not evaluator-sealed")
        if request.source_sha256 != baseline.source_sha256:
            raise EvaluatorDryRunError("hidden correctness uses another source")
        if request.apk_sha256 != baseline.apk_artifact["sha256"]:
            raise EvaluatorDryRunError("hidden correctness uses another APK")

    @staticmethod
    def _validate_restored(plan, baseline, attempt) -> None:
        request = attempt.request
        if request.run_id != plan.run_id or request.role != "RESTORED":
            raise EvaluatorDryRunError("restored build identity is invalid")
        if request.task_root.resolve() != plan.evaluator_workspace.resolve():
            raise EvaluatorDryRunError("restored build uses another workspace")
        if not request.args or request.args[0] != "clean":
            raise EvaluatorDryRunError("restored build is not clean")
        if request.source_relative_path != baseline.request.source_relative_path:
            raise EvaluatorDryRunError("restored build uses another source root")

    def run(self, plan: EvaluatorDryRunPlan) -> EvaluatorDryRunExecution:
        started_at = self.clock()
        baseline, public_correctness = self._baseline(plan)
        materialization = self.materialize(
            plan.hidden_root, plan.public_root, plan.evaluator_workspace
        )

        hidden = cleanup = None
        try:
            hidden = plan.hidden_correctness(
                plan.evaluator_workspace.resolve(), materialization, baseline
            )
            self._validate_hidden(plan, baseline, materialization, hidden)
        finally:
            cleanup = plan.cleanup(plan.evaluator_workspace.resolve())
        if hidden is None:
            raise EvaluatorDryRunError("hidden correctness did not produce an attempt")
        if cleanup is None or getattr(cleanup, "status", None) != "PASS":
            raise EvaluatorDryRunError("evaluator cleanup did not pass")

        restored = plan.restored_build(
            plan.evaluator_workspace.resolve(), materialization, baseline
        )
        self._validate_restored(plan, baseline, restored)
        completed_at = self.clock()
        result = _BUILDER.build_android_dry_run(
            result_id=plan.result_id,
            task_id=plan.task_id,
            task_version=plan.task_version,
            run_id=plan.run_id,
            started_at=started_at,
            completed_at=completed_at,
            task_package_sha256=plan.task_package_sha256,
            toolchain_lock_sha256=plan.toolchain_lock_sha256,
            static_validation=plan.static_validation,
            preflight=plan.preflight,
            baseline_build=_BUILDER.executed_build(baseline),
            restored_build=_BUILDER.executed_build(restored),
            public_correctness=_BUILDER.executed_correctness(public_correctness),
            hidden_correctness=_BUILDER.executed_correctness(hidden),
        )
        return EvaluatorDryRunExecution(
            plan, materialization, hidden, cleanup, restored, result
        )
