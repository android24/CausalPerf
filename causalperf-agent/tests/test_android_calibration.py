from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from causalperf_agent.android import (
    BenchmarkRunAttempt,
    CalibrationBlockAttempt,
    CalibrationBlockPlan,
    CalibrationBlockStore,
    CalibrationMeasurementExecutionAdapter,
    CalibrationProtocolExecutionAdapter,
    CalibrationSessionProtocol,
    GradleBenchmarkRequest,
    PreflightResult,
    StartupStabilizationAttempt,
    StartupStabilizationRequest,
    digest_tree,
)
from causalperf_agent.execution import (
    ExecutionSnapshot, ExecutionState, ExperimentController, FileRunStore, SimulatedAdapter,
)
from causalperf_agent.policy import (
    GuardedExecutionAdapter, PolicyEngine, RuntimePolicy, ToolRequest,
)
from causalperf_reference.artifacts import digest


SERIAL = "LAB-DEVICE-001"


def sealed(value):
    value["content_sha256"] = digest(value, omit=("content_sha256",))
    return value


class Clock:
    def __init__(self, minute=0):
        self.minute = minute
        self.second = 0

    def __call__(self):
        value = f"2026-08-30T04:{self.minute:02d}:{self.second:02d}Z"
        self.second += 1
        return value


class FakeCollector:
    def __init__(self, status="PASS"):
        self.status = status
        self.calls = []

    def collect(self, *, device_serial, environment_id, requirements):
        self.calls.append((device_serial, environment_id, requirements))
        if self.status != "PASS":
            return PreflightResult("INCONCLUSIVE", ("THERMAL_STATUS_NOT_ALLOWED",))
        snapshot = sealed({
            "schema_version": 1, "id": environment_id,
            "captured_at": "2026-08-30T04:00:00Z",
            "device": {"serial_hash": digest(device_serial), "model": "Pixel",
                       "abi": "arm64-v8a", "api_level": 36,
                       "build_fingerprint_sha256": "1" * 64},
            "runtime": {"battery_percent": 90, "charging": False,
                        "thermal_status": "NONE", "online_cpu_count": 8,
                        "available_memory_mb": 4096, "background_load_percent": 2,
                        "compilation_mode": "none"},
            "toolchain": {"gradle": "9.5.0"},
            "validity": {"status": "PASS", "reason_codes": []},
        })
        return PreflightResult("PASS", (), snapshot)


class FakeStabilizer:
    def __init__(self, status="PASS"):
        self.status = status
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        result = sealed({
            "schema_version": 1, "run_id": request.run_id, "arm": request.arm,
            "status": self.status,
            "reason_codes": [] if self.status == "PASS" else ["STABILIZATION_FAILED"],
            "iterations_required": 3,
            "iterations_completed": 3 if self.status == "PASS" else 1,
            "device_serial_hash": request.device_serial_hash,
            "package_name": request.package_name,
            "launch_component": request.launch_component,
            "source_sha256": request.source_sha256, "apk_sha256": request.apk_sha256,
            "environment_snapshot_id": request.environment_snapshot_id,
            "environment_snapshot_sha256": request.environment_snapshot_sha256,
            "sequence_plan_sha256": request.sequence_plan_sha256,
            "process_facts": [], "started_at": "2026-08-30T04:00:01Z",
            "completed_at": "2026-08-30T04:00:02Z",
        })
        return StartupStabilizationAttempt(
            request, self.status, tuple(result["reason_codes"]), result
        )


class FakeBenchmarkRunner:
    def __init__(self):
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        measured_at = {"A1": "2026-08-30T04:00:03Z", "B": "2026-08-30T04:01:03Z",
                       "A2": "2026-08-30T04:02:03Z"}[request.arm]
        measurements = [{
            "id": f"M-{request.arm}-{index:03d}", "sequence": index,
            "value": 100 + index, "measured_at": measured_at,
            "environment_snapshot_id": request.environment_snapshot_id,
            "source_sha256": request.source_sha256, "apk_sha256": request.apk_sha256,
            "trace_sha256": hashlib.sha256(f"{request.arm}-{index}".encode()).hexdigest(),
            "included": True,
        } for index in range(30)]
        measurement_set = sealed({
            "schema_version": 1, "id": f"MS-RUN-CPU-001-{request.arm}",
            "run_id": request.run_id, "partition": "CALIBRATION", "arm": request.arm,
            "metric": request.metric, "unit": request.unit, "measurements": measurements,
            "policy": {"warmup_count": 3, "minimum_included": 30,
                       "max_invalid_percent": 10,
                       "predeclared_exclusion_codes": list(request.predeclared_exclusion_codes)},
        })
        artifacts = tuple({
            "sha256": measurement["trace_sha256"], "kind": "TRACE"
        } for measurement in measurements)
        result = sealed({
            "schema_version": 1, "run_id": request.run_id, "arm": request.arm,
            "partition": "CALIBRATION", "status": "PASS", "reason_codes": [],
            "started_at": measured_at, "completed_at": measured_at,
            "command_sha256": digest(request.command()),
            "device_serial_hash": request.device_serial_hash,
            "source_sha256": request.source_sha256, "apk_sha256": request.apk_sha256,
            "sequence_plan_sha256": request.sequence_plan_sha256,
            "artifact_digests": [value["sha256"] for value in artifacts],
            "stabilization_evidence_sha256": request.stabilization_evidence_sha256,
            "measurement_set_sha256": measurement_set["content_sha256"],
            "stdout_sha256": "2" * 64, "stderr_sha256": "3" * 64,
        })
        return BenchmarkRunAttempt(
            request, "PASS", (), result, measurement_set, artifacts, b"", b"", 0
        )


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "app/src/main"
        self.source.mkdir(parents=True)
        (self.source / "Main.kt").write_text("class Main\n")
        (self.root / "gradlew").write_text("wrapper\n")
        self.apk = self.root / "app/build/app.apk"
        self.apk.parent.mkdir(parents=True)
        self.apk.write_bytes(b"apk")
        self.block_store = CalibrationBlockStore(self.root / "run")

    def protocol(self):
        prediction = sealed({
            "schema_version": 1, "id": "PR-CPU-001", "hypothesis_id": "H-CPU-001",
            "registered_at": "2026-08-30T04:00:10Z",
            "mechanism": "Redundant lookup-table computation blocks the main thread.",
            "primary_metric": "timeToInitialDisplayMs", "expected_direction": "decrease",
            "minimum_effect": {"absolute": 50, "relative_percent": 10,
                               "combination": "maximum"},
            "expected_mechanism_change": [{"metric": "main_thread_cpu_ms",
                                            "direction": "decrease"}],
            "falsification_conditions": ["Main-thread CPU does not decrease."],
        })
        statistics = sealed({
            "schema_version": 1, "id": "SP-CPU-001", "policy_version": "0.1.0",
            "prediction_id": "PR-CPU-001", "registered_at": "2026-08-30T03:59:00Z",
            "design": "a1_b_a2", "minimum_included_per_arm": 30,
            "max_invalid_percent": 10, "max_baseline_drift_percent": 10,
            "bootstrap_resamples": 10000, "confidence_level": 0.95, "seed": 42,
            "multiplicity": {"method": "bonferroni",
                             "family": "protected_secondary_metrics"},
        })
        environment = sealed({
            "schema_version": 1, "id": "EP-CPU-001", "policy_version": "0.1.0",
            "api_level": {"minimum": 34, "maximum": 36},
            "allowed_abis": ["arm64-v8a"], "min_battery_percent": 50,
            "charging": "ANY", "allowed_thermal_statuses": ["NONE", "LIGHT"],
            "expected_online_cpu_count": 8, "min_available_memory_mb": 2048,
            "max_background_load_percent": 20, "compilation_mode": "none",
        })
        intervention = sealed({
            "schema_version": 1, "id": "IP-CPU-001", "hypothesis_id": "H-CPU-001",
            "prediction_id": "PR-CPU-001", "created_at": "2026-08-30T04:00:20Z",
            "intent": "Remove redundant startup computation only.",
            "primary_factor": "lookup computation", "allowed_paths": ["app/src/main"],
            "patch_sha256": "a" * 64, "risk": "LOW",
            "approval": {"required": True, "status": "APPROVED", "approval_id": "AP-1"},
            "rollback": {"strategy": "restore_clean_copy",
                         "baseline_source_sha256": "b" * 64},
        })
        return CalibrationSessionProtocol(
            "RUN-CPU-001-CAL-001", "startup-main-thread-cpu-001",
            prediction, statistics, environment, intervention,
            {"design": "a1_b_a2", "arms": ["A1", "B", "A2"],
             "stabilization_iterations": 3, "measurement_iterations_per_arm": 30,
             "partition": "CALIBRATION"},
        )

    def stabilization_request(self, arm, environment, source_sha=None, apk_sha=None):
        return StartupStabilizationRequest(
            run_id="RUN-CPU-001-CAL-001", arm=arm, task_root=self.root,
            adb_executable="/tools/adb", device_serial=SERIAL,
            device_serial_hash=digest(SERIAL), package_name="dev.causalperf.startup.cpu",
            launch_component="dev.causalperf.startup.cpu/.MainActivity",
            environment={"PATH": "/tools"}, source_relative_path="app/src/main",
            source_sha256=source_sha or digest_tree(self.source),
            apk_relative_path="app/build/app.apk",
            apk_sha256=apk_sha or hashlib.sha256(b"apk").hexdigest(),
            environment_snapshot_id=environment["id"],
            environment_snapshot_sha256=environment["content_sha256"],
            sequence_plan_sha256=self.protocol().sequence_plan_sha256,
        )

    def benchmark_request(self, arm, environment, stabilization, *, args=None,
                          source_sha=None, apk_sha=None):
        return GradleBenchmarkRequest(
            run_id="RUN-CPU-001-CAL-001", arm=arm, task_root=self.root,
            wrapper_relative_path="gradlew",
            args=args or (":macrobenchmark:connectedCheck",),
            environment={"JAVA_HOME": "/jdk"}, timeout_seconds=7200,
            output_limit_bytes=1_000_000, result_limit_bytes=10_000_000,
            result_file_limit=40, source_relative_path="app/src/main",
            source_sha256=source_sha or digest_tree(self.source),
            apk_relative_path="app/build/app.apk", apk_artifact_id=f"AR-APK-{arm}",
            apk_sha256=apk_sha or hashlib.sha256(b"apk").hexdigest(),
            result_root_relative_path=f"macrobenchmark/build/{arm}",
            device_serial=SERIAL, device_serial_hash=digest(SERIAL),
            package_name="dev.causalperf.startup.cpu", partition="CALIBRATION",
            sequence_plan_sha256=self.protocol().sequence_plan_sha256,
            environment_snapshot_id=environment["id"],
            metric="timeToInitialDisplayMs", unit="ms", expected_iterations=30,
            warmup_count=3, max_invalid_percent=10,
            predeclared_exclusion_codes=("THERMAL", "MISSING_REQUIRED_ARTIFACT"),
            benchmark_name="coldStartup",
            stabilization_evidence_sha256=stabilization.result["content_sha256"],
        )

    def block_plan(self, state, *, changed_args=False):
        arm = {ExecutionState.MEASURING_A1: "A1", ExecutionState.MEASURING_B: "B",
               ExecutionState.MEASURING_A2: "A2"}[state]
        source_sha = "d" * 64 if arm == "B" else "b" * 64
        apk_sha = "e" * 64 if arm == "B" else "c" * 64
        environment = FakeCollector().collect(
            device_serial=SERIAL, environment_id=f"ENV-CPU-001-{arm}", requirements=None
        ).environment_snapshot
        fake_stab = FakeStabilizer().run(self.stabilization_request(
            arm, environment, source_sha=source_sha, apk_sha=apk_sha
        ))
        authorized = self.benchmark_request(
            arm, environment, fake_stab, source_sha=source_sha, apk_sha=apk_sha
        ).tool_request(
            "2026-08-30T03:59:00Z"
        )

        def make_stabilization(value):
            return self.stabilization_request(
                arm, value, source_sha=source_sha, apk_sha=apk_sha
            )

        def make_benchmark(value, stabilization):
            args = (":macrobenchmark:changed",) if changed_args else None
            return self.benchmark_request(
                arm, value, stabilization, args=args,
                source_sha=source_sha, apk_sha=apk_sha,
            )

        return CalibrationBlockPlan(
            state, f"ENV-CPU-001-{arm}", authorized,
            make_stabilization, make_benchmark,
        )

    def test_measurement_block_composes_environment_stabilization_and_benchmark(self):
        plans = {state: self.block_plan(state) for state in (
            ExecutionState.MEASURING_A1, ExecutionState.MEASURING_B,
            ExecutionState.MEASURING_A2,
        )}
        collector, stabilizer, benchmark = FakeCollector(), FakeStabilizer(), FakeBenchmarkRunner()
        adapter = CalibrationMeasurementExecutionAdapter(
            SimulatedAdapter(), collector, SimpleNamespace(), SERIAL,
            stabilizer, benchmark, plans, self.block_store,
        )
        result = adapter.execute(
            ExecutionState.MEASURING_A1, ExecutionSnapshot("RUN-CPU-001-CAL-001")
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(stabilizer.calls), 1)
        self.assertEqual(len(benchmark.calls), 1)
        self.assertEqual(self.block_store.load("A1")["measurement_set"]["arm"], "A1")

    def test_block_environment_failure_stops_before_launch(self):
        plans = {state: self.block_plan(state) for state in (
            ExecutionState.MEASURING_A1, ExecutionState.MEASURING_B,
            ExecutionState.MEASURING_A2,
        )}
        stabilizer, benchmark = FakeStabilizer(), FakeBenchmarkRunner()
        adapter = CalibrationMeasurementExecutionAdapter(
            SimulatedAdapter(), FakeCollector("INCONCLUSIVE"), SimpleNamespace(), SERIAL,
            stabilizer, benchmark, plans, self.block_store,
        )
        result = adapter.execute(
            ExecutionState.MEASURING_A1, ExecutionSnapshot("RUN-CPU-001-CAL-001")
        )
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(stabilizer.calls, [])
        self.assertEqual(benchmark.calls, [])

    def test_executed_benchmark_must_equal_authorized_request(self):
        plans = {state: self.block_plan(state, changed_args=state == ExecutionState.MEASURING_A1)
                 for state in (ExecutionState.MEASURING_A1, ExecutionState.MEASURING_B,
                               ExecutionState.MEASURING_A2)}
        adapter = CalibrationMeasurementExecutionAdapter(
            SimulatedAdapter(), FakeCollector(), SimpleNamespace(), SERIAL,
            FakeStabilizer(), FakeBenchmarkRunner(), plans, self.block_store,
        )
        with self.assertRaisesRegex(ValueError, "differs from authorized"):
            adapter.execute(
                ExecutionState.MEASURING_A1, ExecutionSnapshot("RUN-CPU-001-CAL-001")
            )

    def save_protocol_block(self, arm, source_sha, apk_sha, measured_at, invalid=0):
        state = {"A1": ExecutionState.MEASURING_A1, "B": ExecutionState.MEASURING_B,
                 "A2": ExecutionState.MEASURING_A2}[arm]
        plan = self.block_plan(state)
        environment = FakeCollector().collect(
            device_serial=SERIAL, environment_id=plan.environment_id, requirements=None
        )
        stab_request = self.stabilization_request(
            arm, environment.environment_snapshot, source_sha=source_sha, apk_sha=apk_sha
        )
        stabilization = FakeStabilizer().run(stab_request)
        benchmark_request = self.benchmark_request(
            arm, environment.environment_snapshot, stabilization,
            source_sha=source_sha, apk_sha=apk_sha,
        )
        benchmark = FakeBenchmarkRunner().run(benchmark_request)
        for index, measurement in enumerate(benchmark.measurement_set["measurements"]):
            measurement["measured_at"] = measured_at
            if index < invalid:
                measurement["included"] = False
                measurement["exclusion_reason"] = "MISSING_REQUIRED_ARTIFACT"
                measurement.pop("trace_sha256", None)
        referenced_traces = {
            value["trace_sha256"] for value in benchmark.measurement_set["measurements"]
            if "trace_sha256" in value
        }
        object.__setattr__(
            benchmark, "artifacts",
            tuple(value for value in benchmark.artifacts if value["sha256"] in referenced_traces),
        )
        benchmark.measurement_set["content_sha256"] = digest(
            benchmark.measurement_set, omit=("content_sha256",)
        )
        benchmark.result["measurement_set_sha256"] = benchmark.measurement_set["content_sha256"]
        benchmark.result["artifact_digests"] = [value["sha256"] for value in benchmark.artifacts]
        benchmark.result["content_sha256"] = digest(
            benchmark.result, omit=("content_sha256",)
        )
        attempt = CalibrationBlockAttempt(
            plan, environment, stabilization, benchmark, "PASS", ()
        )
        self.block_store.save(attempt)

    def populate_blocks(self, *, invalid_a1=0, restored_source=None):
        baseline_source, baseline_apk = "b" * 64, "c" * 64
        treatment_source, treatment_apk = "d" * 64, "e" * 64
        self.save_protocol_block("A1", baseline_source, baseline_apk,
                                 "2026-08-30T04:00:03Z", invalid_a1)
        self.save_protocol_block("B", treatment_source, treatment_apk,
                                 "2026-08-30T04:01:03Z")
        self.save_protocol_block("A2", restored_source or baseline_source, baseline_apk,
                                 "2026-08-30T04:02:03Z")

    def test_protocol_registration_and_verification_pass(self):
        self.populate_blocks()
        protocol = self.protocol()
        adapter = CalibrationProtocolExecutionAdapter(
            SimulatedAdapter(), protocol, self.block_store, Clock(3)
        )
        registration = adapter.execute(
            ExecutionState.REGISTERING, ExecutionSnapshot(protocol.run_id)
        )
        self.assertEqual(registration.status, "PASS")
        snapshot = ExecutionSnapshot(protocol.run_id)
        snapshot.artifact_digests.extend(registration.output_digests)
        self.assertEqual(
            adapter.execute(ExecutionState.APPLYING_INTERVENTION, snapshot).status, "PASS"
        )
        result = adapter.execute(ExecutionState.VERIFYING, snapshot)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(adapter.verification_summary["partition"], "CALIBRATION")

    def test_invalid_sample_is_retained_then_fails_minimum_included_gate(self):
        self.populate_blocks(invalid_a1=1)
        adapter = CalibrationProtocolExecutionAdapter(
            SimulatedAdapter(), self.protocol(), self.block_store, Clock(3)
        )
        result = adapter.execute(
            ExecutionState.VERIFYING, ExecutionSnapshot("RUN-CPU-001-CAL-001")
        )
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertIn("MINIMUM_INCLUDED_NOT_MET:A1", result.reason_codes)
        stored = self.block_store.load("A1")["measurement_set"]["measurements"][0]
        self.assertFalse(stored["included"])

    def test_restored_source_mismatch_is_fail(self):
        self.populate_blocks(restored_source="f" * 64)
        adapter = CalibrationProtocolExecutionAdapter(
            SimulatedAdapter(), self.protocol(), self.block_store, Clock(3)
        )
        result = adapter.execute(
            ExecutionState.VERIFYING, ExecutionSnapshot("RUN-CPU-001-CAL-001")
        )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("BASELINE_RESTORATION_MISMATCH", result.reason_codes)

    def test_tampered_persisted_block_is_rejected(self):
        self.populate_blocks()
        path = self.root / "run/calibration-block-A1.json"
        value = json.loads(path.read_text())
        value["measurement_set"]["measurements"][0]["value"] = 0
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "content_sha256"):
            self.block_store.load("A1")

    def test_protocol_rejects_non_calibration_or_unapproved_intervention(self):
        protocol = self.protocol()
        with self.assertRaisesRegex(ValueError, "CALIBRATION"):
            CalibrationSessionProtocol(
                protocol.run_id, protocol.task_id, protocol.prediction,
                protocol.statistical_policy, protocol.environment_policy,
                protocol.intervention, protocol.sequence_plan, partition="DEVELOPMENT",
            )
        intervention = dict(protocol.intervention)
        intervention["approval"] = {"required": True, "status": "PENDING"}
        intervention["content_sha256"] = digest(intervention, omit=("content_sha256",))
        with self.assertRaisesRegex(ValueError, "approval"):
            CalibrationSessionProtocol(
                protocol.run_id, protocol.task_id, protocol.prediction,
                protocol.statistical_policy, protocol.environment_policy,
                intervention, protocol.sequence_plan,
            )

    def runtime_policy(self):
        return RuntimePolicy(sealed({
            "schema_version": 1, "id": "POL-CPU-001-CAL",
            "run_id": "RUN-CPU-001-CAL-001", "network": "denied",
            "readable_paths": ["app"], "writable_paths": ["app/src/main"],
            "protected_paths": ["app/src/androidTest", "macrobenchmark"],
            "allowed_executables": {"build_variant": ["./gradlew"],
                                    "run_benchmark": ["./gradlew"]},
            "allowed_working_directories": ["."],
            "allowed_environment_keys": ["JAVA_HOME"],
            "device_serial_hash": digest(SERIAL),
            "package_name": "dev.causalperf.startup.cpu",
            "allowed_partitions": ["CALIBRATION"],
            "task_approved_risks": ["R0", "R1"],
            "allow_external_publication": False,
            "budgets": {"tool_calls": 10, "wall_time_seconds": 30000,
                        "experiments": 3, "patch_files": 8, "patch_lines": 500,
                        "output_bytes": 10_000_000},
        }))

    def test_full_controller_runs_a1_b_a2_with_policy_ledger_and_checkpoint(self):
        plans = {state: self.block_plan(state) for state in (
            ExecutionState.MEASURING_A1, ExecutionState.MEASURING_B,
            ExecutionState.MEASURING_A2,
        )}
        base = SimulatedAdapter()
        measurement = CalibrationMeasurementExecutionAdapter(
            base, FakeCollector(), SimpleNamespace(), SERIAL,
            FakeStabilizer(), FakeBenchmarkRunner(), plans, self.block_store,
        )
        protocol = CalibrationProtocolExecutionAdapter(
            measurement, self.protocol(), self.block_store, Clock(3)
        )
        guarded = GuardedExecutionAdapter(
            protocol,
            PolicyEngine(self.runtime_policy(), clock=lambda: "2026-08-30T03:59:30Z"),
            lambda state, snapshot: measurement.tool_request(state),
        )
        run_store = FileRunStore(self.root / "controller-run")
        controller = ExperimentController(
            "RUN-CPU-001-CAL-001", guarded, clock=Clock(5), store=run_store
        )
        result = controller.run()
        self.assertEqual(result.state, ExecutionState.COMPLETED)
        self.assertEqual(set(self.block_store.load_all()), {"A1", "B", "A2"})
        self.assertEqual([record["tool_id"] for record in guarded.audit_records],
                         ["run_benchmark", "run_benchmark", "run_benchmark"])
        self.assertEqual(result.budget_usage["experiments"], 3)
        self.assertEqual(base.workspace, "BASELINE")
        self.assertEqual(run_store.load()[0]["state"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
