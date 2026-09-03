from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

import jsonschema
from causalperf_reference.artifacts import digest, verify_content_digest

from causalperf_agent.execution.adapter import ExecutionAdapter, RecoveryObservation
from causalperf_agent.execution.model import ExecutionSnapshot, ExecutionState, StepResult
from causalperf_agent.policy.model import ToolRequest

from .benchmark import (
    BenchmarkRunAttempt,
    GradleBenchmarkRequest,
    GradleBenchmarkRunner,
    MEASUREMENT_STATES,
)
from .environment import AndroidEnvironmentCollector, AndroidLabRequirements, PreflightResult
from .stabilization import (
    StartupStabilizationAttempt,
    StartupStabilizationRequest,
    StartupStabilizationRunner,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_ROOT = _REPOSITORY_ROOT / "shared" / "schemas"


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        json.loads((_SCHEMA_ROOT / name).read_text(encoding="utf-8")),
        format_checker=jsonschema.FormatChecker(),
    )


_PROTOCOL_SCHEMAS = {
    "prediction": _validator("prediction.schema.json"),
    "statistical_policy": _validator("statistical-policy.schema.json"),
    "environment_policy": _validator("environment-policy.schema.json"),
    "intervention": _validator("intervention-plan.schema.json"),
}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("calibration timestamp lacks timezone")
    return parsed


@dataclass(frozen=True)
class CalibrationSessionProtocol:
    run_id: str
    task_id: str
    prediction: dict
    statistical_policy: dict
    environment_policy: dict
    intervention: dict
    sequence_plan: dict
    partition: str = "CALIBRATION"

    def __post_init__(self) -> None:
        if self.partition != "CALIBRATION":
            raise ValueError("Phase 1B.4 may write only to CALIBRATION")
        documents = {
            "prediction": copy.deepcopy(self.prediction),
            "statistical_policy": copy.deepcopy(self.statistical_policy),
            "environment_policy": copy.deepcopy(self.environment_policy),
            "intervention": copy.deepcopy(self.intervention),
        }
        for name, document in documents.items():
            _PROTOCOL_SCHEMAS[name].validate(document)
            verify_content_digest(document)
            object.__setattr__(self, name, document)

        sequence = copy.deepcopy(self.sequence_plan)
        required = {
            "design", "arms", "stabilization_iterations",
            "measurement_iterations_per_arm", "partition",
        }
        if set(sequence) != required:
            raise ValueError("calibration sequence plan fields are not frozen")
        if sequence != {
            "design": "a1_b_a2",
            "arms": ["A1", "B", "A2"],
            "stabilization_iterations": 3,
            "measurement_iterations_per_arm": 30,
            "partition": "CALIBRATION",
        }:
            raise ValueError("unsupported Phase 1B.4 sequence plan")
        object.__setattr__(self, "sequence_plan", sequence)

        prediction = self.prediction
        statistics = self.statistical_policy
        intervention = self.intervention
        if statistics["prediction_id"] != prediction["id"]:
            raise ValueError("statistical policy references another prediction")
        if intervention["prediction_id"] != prediction["id"]:
            raise ValueError("intervention references another prediction")
        if intervention["hypothesis_id"] != prediction["hypothesis_id"]:
            raise ValueError("intervention references another hypothesis")
        if statistics["design"] != sequence["design"]:
            raise ValueError("statistical design differs from sequence plan")
        if statistics["minimum_included_per_arm"] != sequence["measurement_iterations_per_arm"]:
            raise ValueError("Phase 1B.4 requires 30 included measurements per arm")
        if intervention["rollback"]["baseline_source_sha256"] == intervention["patch_sha256"]:
            raise ValueError("baseline source and patch identities cannot be equal")
        if _parse_time(prediction["registered_at"]) > _parse_time(intervention["created_at"]):
            raise ValueError("prediction was registered after intervention creation")
        if _parse_time(statistics["registered_at"]) > _parse_time(intervention["created_at"]):
            raise ValueError("statistical policy was registered after intervention creation")
        approval = intervention["approval"]
        if approval["required"] and approval["status"] != "APPROVED":
            raise ValueError("calibration intervention approval is not active")

    @property
    def sequence_plan_sha256(self) -> str:
        return digest(self.sequence_plan)

    @property
    def content_sha256(self) -> str:
        return digest({
            "run_id": self.run_id,
            "task_id": self.task_id,
            "partition": self.partition,
            "prediction_sha256": self.prediction["content_sha256"],
            "statistical_policy_sha256": self.statistical_policy["content_sha256"],
            "environment_policy_sha256": self.environment_policy["content_sha256"],
            "intervention_sha256": self.intervention["content_sha256"],
            "sequence_plan_sha256": self.sequence_plan_sha256,
        })

    @property
    def initial_content_sha256(self) -> str:
        """Session-open commitment; prediction/intervention are registered after A1."""
        return digest({
            "run_id": self.run_id,
            "task_id": self.task_id,
            "partition": self.partition,
            "statistical_policy_sha256": self.statistical_policy["content_sha256"],
            "environment_policy_sha256": self.environment_policy["content_sha256"],
            "sequence_plan_sha256": self.sequence_plan_sha256,
        })


StabilizationRequestFactory = Callable[[dict], StartupStabilizationRequest]
BenchmarkRequestFactory = Callable[[dict, StartupStabilizationAttempt], GradleBenchmarkRequest]


@dataclass(frozen=True)
class CalibrationBlockPlan:
    state: ExecutionState
    environment_id: str
    authorization_request: ToolRequest
    stabilization_request_factory: StabilizationRequestFactory
    benchmark_request_factory: BenchmarkRequestFactory

    def __post_init__(self) -> None:
        if self.state not in MEASUREMENT_STATES:
            raise ValueError("calibration block must target a measurement state")
        if not re.fullmatch(r"ENV-[A-Z0-9-]+", self.environment_id):
            raise ValueError("invalid block environment ID")
        if self.authorization_request.tool_id != "run_benchmark":
            raise ValueError("calibration block requires run_benchmark authorization")


@dataclass(frozen=True)
class CalibrationBlockAttempt:
    plan: CalibrationBlockPlan
    preflight: PreflightResult
    stabilization: StartupStabilizationAttempt | None
    benchmark: BenchmarkRunAttempt | None
    status: str
    reason_codes: tuple[str, ...]

    @property
    def output_digests(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.preflight.environment_snapshot is not None:
            values.append(self.preflight.environment_snapshot["content_sha256"])
        if self.stabilization is not None:
            values.extend(self.stabilization.output_digests)
        if self.benchmark is not None:
            values.extend(self.benchmark.output_digests)
        return tuple(dict.fromkeys(values))


class CalibrationBlockStore:
    """Run-scoped atomic records used to resume after completed measurement blocks."""

    def __init__(self, run_directory: str | Path):
        self.run_directory = Path(run_directory)

    def _path(self, arm: str) -> Path:
        if arm not in {"A1", "B", "A2"}:
            raise ValueError("invalid calibration arm")
        return self.run_directory / f"calibration-block-{arm}.json"

    def save(self, attempt: CalibrationBlockAttempt) -> dict:
        benchmark = attempt.benchmark
        snapshot = attempt.preflight.environment_snapshot
        if attempt.status != "PASS" or benchmark is None or benchmark.measurement_set is None or snapshot is None:
            raise ValueError("only complete passing calibration blocks may be checkpointed")
        arm = MEASUREMENT_STATES[attempt.plan.state]
        document = {
            "schema_version": 1,
            "run_id": benchmark.request.run_id,
            "arm": arm,
            "environment_snapshot": copy.deepcopy(snapshot),
            "stabilization": copy.deepcopy(attempt.stabilization.result),
            "benchmark_result": copy.deepcopy(benchmark.result),
            "measurement_set": copy.deepcopy(benchmark.measurement_set),
            "artifacts": [copy.deepcopy(value) for value in benchmark.artifacts],
        }
        document["content_sha256"] = digest(document, omit=("content_sha256",))
        self._validate(document, arm)
        path = self._path(arm)
        if path.exists() or path.is_symlink():
            raise ValueError(f"calibration block already exists: {arm}")
        self.run_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.run_directory / f".calibration-block-{arm}.tmp"
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return document

    def load(self, arm: str) -> dict:
        document = json.loads(self._path(arm).read_text(encoding="utf-8"))
        self._validate(document, arm)
        return document

    @staticmethod
    def _validate(document: dict, arm: str) -> None:
        verify_content_digest(document)
        if document.get("arm") != arm:
            raise ValueError("calibration block arm mismatch")
        for key in ("environment_snapshot", "stabilization", "benchmark_result", "measurement_set"):
            verify_content_digest(document[key])
        run_id = document.get("run_id")
        environment = document["environment_snapshot"]
        stabilization = document["stabilization"]
        benchmark = document["benchmark_result"]
        measurement_set = document["measurement_set"]
        if any(value.get("run_id") != run_id for value in (stabilization, benchmark, measurement_set)):
            raise ValueError("calibration block contains another run")
        if any(value.get("arm") != arm for value in (stabilization, benchmark, measurement_set)):
            raise ValueError("calibration block contains another arm")
        if benchmark.get("measurement_set_sha256") != measurement_set["content_sha256"]:
            raise ValueError("benchmark result is not bound to its MeasurementSet")
        if benchmark.get("stabilization_evidence_sha256") != stabilization["content_sha256"]:
            raise ValueError("benchmark result is not bound to stabilization")
        if stabilization.get("environment_snapshot_id") != environment["id"]:
            raise ValueError("stabilization is not bound to the block environment")
        if stabilization.get("environment_snapshot_sha256") != environment["content_sha256"]:
            raise ValueError("stabilization environment digest mismatch")
        artifact_digests = {value["sha256"] for value in document["artifacts"]}
        if set(benchmark.get("artifact_digests", [])) != artifact_digests:
            raise ValueError("benchmark artifact registry mismatch")
        trace_digests = {
            value["trace_sha256"]
            for value in measurement_set["measurements"]
            if "trace_sha256" in value
        }
        if not trace_digests.issubset(artifact_digests):
            raise ValueError("MeasurementSet references an unsealed trace")

    def load_all(self) -> dict[str, dict]:
        return {arm: self.load(arm) for arm in ("A1", "B", "A2")}


@dataclass
class CalibrationMeasurementExecutionAdapter:
    delegate: ExecutionAdapter
    collector: AndroidEnvironmentCollector
    requirements: AndroidLabRequirements
    device_serial: str = field(repr=False)
    stabilizer: StartupStabilizationRunner
    benchmark_runner: GradleBenchmarkRunner
    blocks: Mapping[ExecutionState, CalibrationBlockPlan]
    store: CalibrationBlockStore
    attempts: dict[ExecutionState, CalibrationBlockAttempt] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if set(self.blocks) != set(MEASUREMENT_STATES):
            raise ValueError("A1, B and A2 calibration block plans are all required")
        if any(state != plan.state for state, plan in self.blocks.items()):
            raise ValueError("calibration block plan state mismatch")

    def tool_request(self, state: ExecutionState) -> ToolRequest | None:
        plan = self.blocks.get(state)
        return plan.authorization_request if plan is not None else None

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        plan = self.blocks.get(state)
        if plan is None:
            return self.delegate.execute(state, snapshot)
        arm = MEASUREMENT_STATES[state]
        preflight = self.collector.collect(
            device_serial=self.device_serial,
            environment_id=plan.environment_id,
            requirements=self.requirements,
        )
        if preflight.status != "PASS" or preflight.environment_snapshot is None:
            attempt = CalibrationBlockAttempt(
                plan, preflight, None, None, "INCONCLUSIVE", preflight.reason_codes
            )
            self.attempts[state] = attempt
            return StepResult("INCONCLUSIVE", reason_codes=attempt.reason_codes)
        environment = preflight.environment_snapshot
        if environment["device"]["serial_hash"] != digest(self.device_serial):
            return StepResult("FAIL", reason_codes=("BLOCK_DEVICE_IDENTITY_MISMATCH",))

        stabilization_request = plan.stabilization_request_factory(environment)
        self._validate_stabilization(snapshot, arm, environment, stabilization_request)
        stabilization = self.stabilizer.run(stabilization_request)
        if stabilization.status != "PASS":
            attempt = CalibrationBlockAttempt(
                plan, preflight, stabilization, None,
                stabilization.status, stabilization.reason_codes,
            )
            self.attempts[state] = attempt
            return StepResult(
                stabilization.status,
                output_digests=attempt.output_digests,
                reason_codes=stabilization.reason_codes,
            )

        benchmark_request = plan.benchmark_request_factory(environment, stabilization)
        self._validate_benchmark(
            snapshot, arm, environment, stabilization, plan, benchmark_request
        )
        benchmark = self.benchmark_runner.run(benchmark_request)
        attempt = CalibrationBlockAttempt(
            plan, preflight, stabilization, benchmark,
            benchmark.status, benchmark.reason_codes,
        )
        self.attempts[state] = attempt
        if attempt.status == "PASS":
            self.store.save(attempt)
        return StepResult(
            attempt.status,
            output_digests=attempt.output_digests,
            reason_codes=attempt.reason_codes,
        )

    def _validate_stabilization(self, snapshot, arm, environment, request) -> None:
        if request.run_id != snapshot.run_id or request.arm != arm:
            raise ValueError("stabilization run/arm binding mismatch")
        if request.device_serial != self.device_serial:
            raise ValueError("stabilization selects another device")
        if request.environment_snapshot_id != environment["id"]:
            raise ValueError("stabilization uses another environment snapshot")
        if request.environment_snapshot_sha256 != environment["content_sha256"]:
            raise ValueError("stabilization environment digest mismatch")
        if request.iterations != 3:
            raise ValueError("stabilization iteration count drift")

    @staticmethod
    def _validate_benchmark(snapshot, arm, environment, stabilization, plan, request) -> None:
        stab = stabilization.request
        if request.run_id != snapshot.run_id or request.arm != arm:
            raise ValueError("benchmark run/arm binding mismatch")
        if request.partition != "CALIBRATION":
            raise ValueError("calibration benchmark uses another partition")
        if request.device_serial_hash != stab.device_serial_hash:
            raise ValueError("benchmark uses another device")
        if request.package_name != stab.package_name:
            raise ValueError("benchmark uses another package")
        if request.source_sha256 != stab.source_sha256 or request.apk_sha256 != stab.apk_sha256:
            raise ValueError("benchmark inputs differ from stabilization")
        if request.environment_snapshot_id != environment["id"]:
            raise ValueError("benchmark uses another environment snapshot")
        if request.warmup_count != stab.iterations:
            raise ValueError("benchmark warmup metadata differs from stabilization")
        if request.stabilization_evidence_sha256 != stabilization.result["content_sha256"]:
            raise ValueError("benchmark is not bound to stabilization evidence")
        if request.sequence_plan_sha256 != stab.sequence_plan_sha256:
            raise ValueError("benchmark sequence plan differs from stabilization")
        actual = request.tool_request(plan.authorization_request.requested_at)
        if actual.request_sha256 != plan.authorization_request.request_sha256:
            raise ValueError("executed benchmark differs from authorized request")

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)


@dataclass
class CalibrationProtocolExecutionAdapter:
    delegate: ExecutionAdapter
    protocol: CalibrationSessionProtocol
    store: CalibrationBlockStore
    clock: Callable[[], str]
    verification_summary: dict | None = field(default=None, init=False)

    def execute(self, state: ExecutionState, snapshot: ExecutionSnapshot) -> StepResult:
        if snapshot.run_id != self.protocol.run_id:
            return StepResult("FAIL", reason_codes=("CALIBRATION_RUN_ID_MISMATCH",))
        if state == ExecutionState.VALIDATING:
            base = self.delegate.execute(state, snapshot)
            if base.status != "PASS":
                return base
            return StepResult("PASS", base.output_digests + (self.protocol.initial_content_sha256,))
        if state == ExecutionState.REGISTERING:
            try:
                a1 = self.store.load("A1")
            except (OSError, ValueError, json.JSONDecodeError):
                return StepResult("INCONCLUSIVE", reason_codes=("A1_NOT_SEALED_BEFORE_REGISTRATION",))
            last_a1 = max(
                _parse_time(value["measured_at"])
                for value in a1["measurement_set"]["measurements"]
            )
            if _parse_time(self.protocol.prediction["registered_at"]) <= last_a1:
                return StepResult(
                    "FAIL", reason_codes=("PREDICTION_REGISTERED_BEFORE_A1_COMPLETE",)
                )
            base = self.delegate.execute(state, snapshot)
            if base.status != "PASS":
                return base
            return StepResult(
                "PASS",
                base.output_digests + (
                    self.protocol.prediction["content_sha256"],
                    self.protocol.statistical_policy["content_sha256"],
                    self.protocol.intervention["content_sha256"],
                ),
            )
        if state == ExecutionState.APPLYING_INTERVENTION:
            required = {
                self.protocol.prediction["content_sha256"],
                self.protocol.statistical_policy["content_sha256"],
                self.protocol.intervention["content_sha256"],
            }
            if not required.issubset(snapshot.artifact_digests):
                return StepResult("FAIL", reason_codes=("INTERVENTION_BEFORE_REGISTRATION",))
            return self.delegate.execute(state, snapshot)
        if state == ExecutionState.VERIFYING:
            base = self.delegate.execute(state, snapshot)
            if base.status != "PASS":
                return base
            status, reasons, summary = self._verify_blocks()
            self.verification_summary = summary
            return StepResult(
                status,
                base.output_digests + (summary["content_sha256"],),
                tuple(reasons),
            )
        return self.delegate.execute(state, snapshot)

    def _verify_blocks(self) -> tuple[str, list[str], dict]:
        try:
            blocks = self.store.load_all()
        except (OSError, ValueError, json.JSONDecodeError):
            summary = self._summary("INCONCLUSIVE", ["CALIBRATION_BLOCK_MISSING"], {})
            return "INCONCLUSIVE", summary["reason_codes"], summary

        reasons: list[str] = []
        failed: list[str] = []
        sets = {arm: block["measurement_set"] for arm, block in blocks.items()}
        policy = self.protocol.statistical_policy
        expected = self.protocol.sequence_plan["measurement_iterations_per_arm"]
        for arm, measurement_set in sets.items():
            measurements = measurement_set["measurements"]
            included = sum(value["included"] for value in measurements)
            invalid_percent = 100.0 * (len(measurements) - included) / len(measurements)
            if len(measurements) != expected:
                reasons.append(f"ITERATION_COUNT_MISMATCH:{arm}")
            if included < policy["minimum_included_per_arm"]:
                reasons.append(f"MINIMUM_INCLUDED_NOT_MET:{arm}")
            if invalid_percent > policy["max_invalid_percent"]:
                reasons.append(f"INVALID_SAMPLE_LIMIT_EXCEEDED:{arm}")
            if measurement_set["partition"] != "CALIBRATION" or measurement_set["arm"] != arm:
                failed.append(f"MEASUREMENT_IDENTITY_MISMATCH:{arm}")
            if measurement_set["metric"] != self.protocol.prediction["primary_metric"]:
                failed.append(f"PRIMARY_METRIC_MISMATCH:{arm}")
            if measurement_set["policy"]["warmup_count"] != 3:
                failed.append(f"STABILIZATION_COUNT_MISMATCH:{arm}")
            if measurement_set["policy"]["minimum_included"] != policy["minimum_included_per_arm"]:
                failed.append(f"MEASUREMENT_POLICY_MINIMUM_MISMATCH:{arm}")
            if measurement_set["policy"]["max_invalid_percent"] != policy["max_invalid_percent"]:
                failed.append(f"MEASUREMENT_POLICY_INVALID_LIMIT_MISMATCH:{arm}")
            registered_codes = set(measurement_set["policy"]["predeclared_exclusion_codes"])
            if any(
                not value["included"] and value.get("exclusion_reason") not in registered_codes
                for value in measurements
            ):
                failed.append(f"UNREGISTERED_EXCLUSION:{arm}")

        a1_first = min(_parse_time(value["measured_at"]) for value in sets["A1"]["measurements"])
        b_first = min(_parse_time(value["measured_at"]) for value in sets["B"]["measurements"])
        a2_first = min(_parse_time(value["measured_at"]) for value in sets["A2"]["measurements"])
        if not a1_first < b_first < a2_first:
            failed.append("A1_B_A2_ORDER_VIOLATION")
        if _parse_time(self.protocol.prediction["registered_at"]) >= b_first:
            failed.append("PREDICTION_NOT_REGISTERED_BEFORE_TREATMENT")
        if _parse_time(self.protocol.statistical_policy["registered_at"]) >= b_first:
            failed.append("STATISTICAL_POLICY_NOT_REGISTERED_BEFORE_TREATMENT")

        source = {
            arm: {value["source_sha256"] for value in item["measurements"]}
            for arm, item in sets.items()
        }
        apk = {
            arm: {value["apk_sha256"] for value in item["measurements"]}
            for arm, item in sets.items()
        }
        if any(len(value) != 1 for value in source.values()) or any(len(value) != 1 for value in apk.values()):
            failed.append("WITHIN_BLOCK_ARTIFACT_DRIFT")
        else:
            if source["A1"] != source["A2"] or apk["A1"] != apk["A2"]:
                failed.append("BASELINE_RESTORATION_MISMATCH")
            if source["B"] == source["A1"]:
                failed.append("TREATMENT_SOURCE_NOT_CHANGED")
            expected_baseline = self.protocol.intervention["rollback"]["baseline_source_sha256"]
            if source["A1"] != {expected_baseline}:
                failed.append("BASELINE_SOURCE_NOT_BOUND_TO_ROLLBACK")

        devices = [blocks[arm]["environment_snapshot"]["device"] for arm in ("A1", "B", "A2")]
        if devices[1:] != devices[:-1]:
            failed.append("DEVICE_IDENTITY_CHANGED_BETWEEN_BLOCKS")
        for arm, block in blocks.items():
            environment = block["environment_snapshot"]
            if environment["validity"]["status"] != "PASS":
                reasons.append(f"BLOCK_ENVIRONMENT_INVALID:{arm}")
            reasons.extend(
                f"BLOCK_ENVIRONMENT_POLICY:{arm}:{reason}"
                for reason in _environment_policy_reasons(
                    environment, self.protocol.environment_policy
                )
            )

        measurement_ids = [
            value["id"] for measurement_set in sets.values()
            for value in measurement_set["measurements"]
        ]
        if len(measurement_ids) != len(set(measurement_ids)):
            failed.append("MEASUREMENT_ID_REUSED_ACROSS_ARMS")
        trace_digests = [
            value["trace_sha256"] for measurement_set in sets.values()
            for value in measurement_set["measurements"] if "trace_sha256" in value
        ]
        if len(trace_digests) != len(set(trace_digests)):
            failed.append("TRACE_REUSED_ACROSS_ARMS")

        all_reasons = list(dict.fromkeys(failed + reasons))
        status = "FAIL" if failed else ("INCONCLUSIVE" if reasons else "PASS")
        summary = self._summary(
            status,
            all_reasons,
            {arm: blocks[arm]["content_sha256"] for arm in ("A1", "B", "A2")},
        )
        return status, all_reasons, summary

    def _summary(self, status: str, reasons: list[str], blocks: dict) -> dict:
        value = {
            "schema_version": 1,
            "run_id": self.protocol.run_id,
            "task_id": self.protocol.task_id,
            "partition": "CALIBRATION",
            "verified_at": self.clock(),
            "protocol_sha256": self.protocol.content_sha256,
            "block_digests": blocks,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons)),
        }
        value["content_sha256"] = digest(value, omit=("content_sha256",))
        return value

    def inspect_recovery(self, snapshot: ExecutionSnapshot) -> RecoveryObservation:
        return self.delegate.inspect_recovery(snapshot)

    def rollback(self, snapshot: ExecutionSnapshot) -> StepResult:
        return self.delegate.rollback(snapshot)


def _environment_policy_reasons(snapshot: dict, policy: dict) -> list[str]:
    device = snapshot["device"]
    runtime = snapshot["runtime"]
    reasons: list[str] = []
    api = policy["api_level"]
    if not api["minimum"] <= device["api_level"] <= api["maximum"]:
        reasons.append("API_LEVEL_OUT_OF_RANGE")
    if device["abi"] not in policy["allowed_abis"]:
        reasons.append("ABI_NOT_ALLOWED")
    if runtime["battery_percent"] < policy["min_battery_percent"]:
        reasons.append("BATTERY_BELOW_MINIMUM")
    if policy["charging"] == "REQUIRED" and not runtime["charging"]:
        reasons.append("CHARGING_REQUIRED")
    if policy["charging"] == "FORBIDDEN" and runtime["charging"]:
        reasons.append("CHARGING_FORBIDDEN")
    if runtime["thermal_status"] not in policy["allowed_thermal_statuses"]:
        reasons.append("THERMAL_STATUS_NOT_ALLOWED")
    if runtime["online_cpu_count"] != policy["expected_online_cpu_count"]:
        reasons.append("ONLINE_CPU_COUNT_MISMATCH")
    if runtime["available_memory_mb"] < policy["min_available_memory_mb"]:
        reasons.append("AVAILABLE_MEMORY_BELOW_MINIMUM")
    if runtime["background_load_percent"] > policy["max_background_load_percent"]:
        reasons.append("BACKGROUND_LOAD_ABOVE_MAXIMUM")
    if runtime["compilation_mode"] != policy["compilation_mode"]:
        reasons.append("COMPILATION_MODE_MISMATCH")
    return reasons
