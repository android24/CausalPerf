import copy
import unittest

from causalperf_reference.artifacts import (
    ContractError, digest, verify_experiment_bundle, verify_partition_registry,
    verify_tool_approval,
)
from causalperf_reference.gates import verify_environment, verify_mechanism, verify_replication
from causalperf_reference.ledger import Ledger
from causalperf_reference.runner import evaluate_bundle


SHA = "a" * 64


def sealed(document):
    document["content_sha256"] = digest(document, omit=("content_sha256",))
    return document


def fixture():
    env = sealed({"schema_version": 1, "id": "ENV-ONE", "captured_at": "2026-01-01T00:00:00Z",
        "device": {"serial_hash": SHA, "model": "test", "abi": "x86_64", "api_level": 36, "build_fingerprint_sha256": SHA},
        "runtime": {"battery_percent": 80, "charging": False, "thermal_status": "NONE", "online_cpu_count": 8, "available_memory_mb": 4096, "background_load_percent": 1, "compilation_mode": "partial"},
        "toolchain": {"adb": "1"}, "validity": {"status": "PASS", "reason_codes": []}})
    hypothesis = sealed({"schema_version": 1, "id": "H-CPU", "created_at": "2026-01-01T00:00:00Z", "mechanism": "Repeated main thread computation delays first frame", "supporting_evidence_ids": [], "contradicting_evidence_ids": [], "alternatives": ["Device scheduling could explain the observation"], "state": "REGISTERED", "prediction_ids": ["PR-CPU"]})
    prediction = sealed({"schema_version": 1, "id": "PR-CPU", "hypothesis_id": "H-CPU", "registered_at": "2026-01-01T00:00:01Z", "mechanism": "Removing repeated work reduces main thread CPU", "primary_metric": "ttid_ms", "expected_direction": "decrease", "minimum_effect": {"absolute": 50, "relative_percent": 10, "combination": "both"}, "expected_mechanism_change": [{"metric": "main_thread_cpu_ms", "direction": "decrease"}], "falsification_conditions": ["CPU evidence does not decrease"]})
    intervention = sealed({"schema_version": 1, "id": "IP-CPU", "hypothesis_id": "H-CPU", "prediction_id": "PR-CPU", "created_at": "2026-01-01T00:00:02Z", "intent": "Compute the lookup table once", "primary_factor": "lookup repetitions", "allowed_paths": ["app/src"], "patch_sha256": SHA, "risk": "LOW", "approval": {"required": False, "status": "NOT_REQUIRED"}, "rollback": {"strategy": "reverse_patch", "baseline_source_sha256": SHA}})
    def arm(name, base, minute):
        measurements = [{"id": f"M-{name}-{i}", "sequence": i, "value": base + i % 3, "measured_at": f"2026-01-01T00:{minute:02d}:{i:02d}Z", "environment_snapshot_id": "ENV-ONE", "source_sha256": SHA, "apk_sha256": SHA, "included": True} for i in range(10)]
        return sealed({"schema_version": 1, "id": f"MS-{name}", "run_id": "RUN-1", "partition": "DEVELOPMENT", "arm": name, "metric": "ttid_ms", "unit": "ms", "measurements": measurements, "policy": {"warmup_count": 0, "minimum_included": 10, "max_invalid_percent": 10, "predeclared_exclusion_codes": ["THERMAL"]}})
    return {"run_id": "RUN-1", "partition": "DEVELOPMENT", "prediction": prediction, "hypothesis": hypothesis, "intervention": intervention, "measurement_sets": [arm("A1", 1000, 2), arm("B", 790, 3), arm("A2", 1002, 4)], "environments": [env], "evidence_by_arm": {"A1": [{"metric": "main_thread_cpu_ms", "value": 500}], "B": [{"metric": "main_thread_cpu_ms", "value": 200}], "A2": [{"metric": "main_thread_cpu_ms", "value": 505}]}, "replication_effects_ms": [205, 210], "integrity_gate": {"status": "PASS", "reason_codes": []}, "correctness_gate": {"status": "PASS", "reason_codes": []}, "statistical_policy": {"absolute_threshold_ms": 50, "relative_threshold_percent": 10, "max_baseline_drift_percent": 5, "bootstrap_resamples": 1000, "seed": 7}}


class ContractTests(unittest.TestCase):
    def test_complete_bundle_runs(self):
        result = evaluate_bundle(fixture())
        self.assertEqual(result["decision"]["verdict"], "CAUSALLY_SUPPORTED")
        self.assertTrue(result["ledger_head_sha256"])

    def test_late_registration_rejected(self):
        item = fixture(); item["prediction"]["registered_at"] = "2026-01-01T00:05:00Z"; sealed(item["prediction"])
        with self.assertRaisesRegex(ContractError, "preregistered"):
            verify_experiment_bundle(item)

    def test_unregistered_exclusion_rejected(self):
        item = fixture(); item["measurement_sets"][0]["policy"]["minimum_included"] = 9; measurement = item["measurement_sets"][0]["measurements"][0]; measurement["included"] = False; measurement["exclusion_reason"] = "OUTLIER"; sealed(item["measurement_sets"][0])
        with self.assertRaisesRegex(ContractError, "unregistered exclusion"):
            verify_experiment_bundle(item)

    def test_mechanism_is_computed(self):
        prediction = fixture()["prediction"]
        gate = verify_mechanism(prediction, {"A1": [{"metric": "main_thread_cpu_ms", "value": 100}], "A2": [], "B": [{"metric": "main_thread_cpu_ms", "value": 200}]})
        self.assertEqual(gate.status, "FAIL")

    def test_measurement_partitions_cannot_be_mixed(self):
        item = fixture(); item["measurement_sets"][1]["partition"] = "QUALIFICATION"; sealed(item["measurement_sets"][1])
        with self.assertRaisesRegex(ContractError, "partitions cannot be mixed"):
            verify_experiment_bundle(item)

    def test_environment_identity_change_is_inconclusive(self):
        original = fixture()["environments"][0]
        environments = [copy.deepcopy(original), copy.deepcopy(original)]; environments[1]["device"]["model"] = "other"
        self.assertEqual(verify_environment(environments).status, "INCONCLUSIVE")

    def test_replication_direction_failure(self):
        self.assertEqual(verify_replication(200, [-10]).status, "FAIL")

    def test_ledger_detects_tampering(self):
        ledger = Ledger("RUN"); ledger.append({"occurred_at": "2026-01-01T00:00:00Z", "actor": "RUNNER", "phase": "A", "kind": "COMPLETION", "inputs": [], "outputs": []}); ledger.events[0]["phase"] = "B"
        with self.assertRaisesRegex(ContractError, "digest"):
            ledger.verify()

    def test_partition_registry_rejects_digest_reuse(self):
        registry = {"schema_version": 1, "task_id": "task", "task_version": "0.1.0", "protocol_version": "1", "entries": [
            {"artifact_sha256": SHA, "partition": "CALIBRATION", "registered_at": "2026-01-01T00:00:00Z"},
            {"artifact_sha256": SHA, "partition": "QUALIFICATION", "registered_at": "2026-01-02T00:00:00Z"}], "created_at": "2026-01-01T00:00:00Z"}
        sealed(registry)
        with self.assertRaisesRegex(ContractError, "reused across partitions"):
            verify_partition_registry(registry)

    def test_tool_approval_binds_exact_request_before_execution(self):
        approval = sealed({"schema_version": 1, "id": "AP-ONE", "run_id": "RUN-1", "risk": "R2", "scope": {"path": "app/src"}, "request_sha256": SHA, "decision": "APPROVED", "decided_at": "2026-01-01T00:00:01Z", "approver_ref": "human:test"})
        call = {"schema_version": 1, "id": "TC-ONE", "run_id": "RUN-1", "requested_at": "2026-01-01T00:00:00Z", "started_at": "2026-01-01T00:00:02Z", "completed_at": "2026-01-01T00:00:03Z", "tool_id": "apply_patch", "risk": "R2", "request_sha256": SHA, "arguments": {}, "policy_decision": {"status": "REQUIRE_APPROVAL", "reason_codes": [], "approval_id": "AP-ONE"}, "status": "SUCCEEDED"}
        verify_tool_approval(call, [approval])
        call["request_sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "exact tool request"):
            verify_tool_approval(call, [approval])

    def test_denied_tool_call_cannot_execute(self):
        call = {"run_id": "RUN-1", "risk": "R3", "requested_at": "2026-01-01T00:00:00Z", "request_sha256": SHA, "policy_decision": {"status": "DENY"}, "status": "SUCCEEDED"}
        with self.assertRaisesRegex(ContractError, "denied tool call"):
            verify_tool_approval(call, [])

    def test_canonical_digest_is_key_order_independent(self):
        self.assertEqual(digest({"b": 2, "a": "中文"}), digest({"a": "中文", "b": 2}))

    def test_content_digest_omits_only_top_level_digest(self):
        document = {"id": "X", "nested": {"content_sha256": SHA}}
        sealed(document)
        original = document["content_sha256"]
        document["nested"]["content_sha256"] = "b" * 64
        self.assertNotEqual(original, digest(document, omit=("content_sha256",)))


if __name__ == "__main__":
    unittest.main()
