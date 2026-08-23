import copy
import unittest

from causalperf_reference.artifacts import (
    ContractError, digest, verify_experiment_bundle, verify_partition_registry,
    verify_tool_approval,
)
from causalperf_reference.gates import (
    verify_correctness,
    verify_environment,
    verify_integrity,
    verify_intervention_isolation,
    verify_mechanism,
    verify_replication,
)
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
    environment_policy = sealed({"schema_version": 1, "id": "EP-STARTUP", "policy_version": "1.0.0",
        "api_level": {"minimum": 36, "maximum": 36}, "allowed_abis": ["x86_64"],
        "min_battery_percent": 50, "charging": "ANY", "allowed_thermal_statuses": ["NONE", "LIGHT"],
        "expected_online_cpu_count": 8, "min_available_memory_mb": 2048,
        "max_background_load_percent": 5, "compilation_mode": "partial"})
    hypothesis = sealed({"schema_version": 1, "id": "H-CPU", "created_at": "2026-01-01T00:00:00Z", "mechanism": "Repeated main thread computation delays first frame", "supporting_evidence_ids": [], "contradicting_evidence_ids": [], "alternatives": ["Device scheduling could explain the observation"], "state": "REGISTERED", "prediction_ids": ["PR-CPU"]})
    prediction = sealed({"schema_version": 1, "id": "PR-CPU", "hypothesis_id": "H-CPU", "registered_at": "2026-01-01T00:00:01Z", "mechanism": "Removing repeated work reduces main thread CPU", "primary_metric": "ttid_ms", "expected_direction": "decrease", "minimum_effect": {"absolute": 50, "relative_percent": 10, "combination": "both"}, "expected_mechanism_change": [{"metric": "main_thread_cpu_ms", "direction": "decrease"}], "protected_secondary_metrics": [{"metric": "rss_mb", "unit": "MB", "regression_direction": "increase", "maximum_regression_percent": 5}], "falsification_conditions": ["CPU evidence does not decrease"]})
    statistical_policy = sealed({"schema_version": 1, "id": "SP-STARTUP", "policy_version": "1.0.0", "prediction_id": "PR-CPU", "registered_at": "2026-01-01T00:00:01Z", "design": "a1_b_a2", "minimum_included_per_arm": 10, "max_invalid_percent": 10, "max_baseline_drift_percent": 5, "bootstrap_resamples": 1000, "confidence_level": 0.95, "seed": 7, "multiplicity": {"method": "bonferroni", "family": "protected_secondary_metrics"}})
    baseline_entries = [
        {"path": "app/src/Main.kt", "sha256": "b" * 64, "size_bytes": 100},
        {"path": "benchmark/StartupBenchmark.kt", "sha256": "c" * 64, "size_bytes": 200},
        {"path": "tests/CorrectnessTest.kt", "sha256": "d" * 64, "size_bytes": 300},
    ]
    treatment_entries = copy.deepcopy(baseline_entries)
    treatment_entries[0]["sha256"] = "e" * 64
    baseline_tree = digest(baseline_entries)
    treatment_tree = digest(treatment_entries)
    def source_manifest(role, entries, tree, identifier):
        item = {"schema_version": 1, "id": identifier, "run_id": "RUN-1", "role": role,
                "created_at": "2026-01-01T00:00:02Z", "entries": entries, "tree_sha256": tree}
        if role == "TREATMENT":
            item["applied_patch_sha256"] = SHA
        return sealed(item)
    baseline_manifest = source_manifest("BASELINE", baseline_entries, baseline_tree, "SM-BASELINE")
    treatment_manifest = source_manifest("TREATMENT", treatment_entries, treatment_tree, "SM-TREATMENT")
    restored_manifest = source_manifest("RESTORED", copy.deepcopy(baseline_entries), baseline_tree, "SM-RESTORED")
    intervention = sealed({"schema_version": 1, "id": "IP-CPU", "hypothesis_id": "H-CPU", "prediction_id": "PR-CPU", "created_at": "2026-01-01T00:00:02Z", "intent": "Compute the lookup table once", "primary_factor": "lookup repetitions", "allowed_paths": ["app/src"], "patch_sha256": SHA, "risk": "LOW", "approval": {"required": False, "status": "NOT_REQUIRED"}, "rollback": {"strategy": "reverse_patch", "baseline_source_sha256": baseline_tree}})
    def correctness(phase, source_manifest_id, minute):
        return sealed({"schema_version": 1, "id": f"CR-{phase}", "run_id": "RUN-1", "phase": phase,
            "started_at": f"2026-01-01T00:{minute:02d}:00Z", "completed_at": f"2026-01-01T00:{minute:02d}:01Z",
            "suite_id": "startup-correctness", "suite_sha256": "f" * 64,
            "source_manifest_id": source_manifest_id, "command_request_sha256": "1" * 64,
            "exit_code": 0, "test_count": 12, "failure_count": 0, "skipped_count": 0,
            "behavior_sha256": "2" * 64, "result_artifact_sha256": "3" * 64})
    def arm(name, base, minute, source_sha256, metric="ttid_ms", unit="ms"):
        metric_id = metric.replace("_", "-").upper()
        measurements = [{"id": f"M-{metric_id}-{name}-{i}", "sequence": i, "value": base + i % 3, "measured_at": f"2026-01-01T00:{minute:02d}:{i:02d}Z", "environment_snapshot_id": "ENV-ONE", "source_sha256": source_sha256, "apk_sha256": SHA, "included": True} for i in range(10)]
        return sealed({"schema_version": 1, "id": f"MS-{metric_id}-{name}", "run_id": "RUN-1", "partition": "DEVELOPMENT", "arm": name, "metric": metric, "unit": unit, "measurements": measurements, "policy": {"warmup_count": 0, "minimum_included": 10, "max_invalid_percent": 10, "predeclared_exclusion_codes": ["THERMAL"]}})
    def evidence(arm, value):
        return sealed({"schema_version": 1, "id": f"EV-{arm}-CPU", "category": "CPU", "metric": "main_thread_cpu_ms", "value": value, "unit": "ms", "source": {"artifact_sha256": "5" * 64, "collector_id": "perfetto-sql", "collector_version": "1"}, "validity": "VALID", "reason_codes": []})
    return {"run_id": "RUN-1", "partition": "DEVELOPMENT", "prediction": prediction, "hypothesis": hypothesis, "intervention": intervention,
        "measurement_sets": [arm("A1", 1000, 2, baseline_tree), arm("B", 790, 3, treatment_tree), arm("A2", 1002, 4, baseline_tree), arm("A1", 100, 2, baseline_tree, "rss_mb", "MB"), arm("B", 100, 3, treatment_tree, "rss_mb", "MB"), arm("A2", 100, 4, baseline_tree, "rss_mb", "MB")],
        "environments": [env], "environment_policy": environment_policy, "integrity_inputs": sealed({"schema_version": 1, "id": "II-RUN-1", "run_id": "RUN-1", "created_at": "2026-01-01T00:04:30Z", "policy_sha256": "4" * 64, "source_manifests": [baseline_manifest, treatment_manifest, restored_manifest], "protected_paths": ["benchmark", "tests"]}),
        "correctness_reports": [correctness("BASELINE", "SM-BASELINE", 1), correctness("TREATMENT", "SM-TREATMENT", 2)],
        "evidence_by_arm": {"A1": [evidence("A1", 500)], "B": [evidence("B", 200)], "A2": [evidence("A2", 505)]}, "replication_effects_ms": [205, 210], "statistical_policy": statistical_policy}


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
        item = fixture(); measurement = item["measurement_sets"][0]["measurements"][0]; measurement["included"] = False; measurement["exclusion_reason"] = "OUTLIER"; sealed(item["measurement_sets"][0])
        with self.assertRaisesRegex(ContractError, "unregistered exclusion"):
            verify_experiment_bundle(item)

    def test_mechanism_is_computed(self):
        prediction = fixture()["prediction"]
        gate = verify_mechanism(prediction, {"A1": [{"metric": "main_thread_cpu_ms", "value": 100}], "A2": [], "B": [{"metric": "main_thread_cpu_ms", "value": 200}]})
        self.assertEqual(gate.status, "FAIL")

    def test_integrity_is_computed_from_source_manifests(self):
        item = fixture()
        gate = verify_integrity(item["intervention"], item["integrity_inputs"])
        self.assertEqual(gate.status, "PASS")

    def test_caller_cannot_self_assert_integrity_pass(self):
        item = fixture()
        treatment = item["integrity_inputs"]["source_manifests"][1]
        treatment["entries"][1]["sha256"] = "9" * 64
        treatment["tree_sha256"] = digest(treatment["entries"])
        sealed(treatment)
        sealed(item["integrity_inputs"])
        for measurement_set in item["measurement_sets"]:
            if measurement_set["arm"] == "B":
                for measurement in measurement_set["measurements"]:
                    measurement["source_sha256"] = treatment["tree_sha256"]
                sealed(measurement_set)
        item["integrity_gate"] = {"status": "PASS", "reason_codes": []}
        result = evaluate_bundle(item)
        self.assertEqual(result["gates"]["integrity"]["status"], "FAIL")
        self.assertIn("PROTECTED_PATH_CHANGED", result["gates"]["integrity"]["reason_codes"])
        self.assertEqual(result["decision"]["verdict"], "INVALID")

    def test_caller_cannot_self_assert_correctness_pass(self):
        item = fixture()
        item["correctness_reports"][1]["exit_code"] = 1
        item["correctness_reports"][1]["failure_count"] = 1
        sealed(item["correctness_reports"][1])
        item["correctness_gate"] = {"status": "PASS", "reason_codes": []}
        result = evaluate_bundle(item)
        self.assertEqual(result["gates"]["correctness"]["status"], "FAIL")
        self.assertEqual(result["decision"]["verdict"], "REJECT")

    def test_missing_correctness_report_is_inconclusive(self):
        item = fixture()
        item["correctness_reports"] = item["correctness_reports"][:1]
        result = evaluate_bundle(item)
        self.assertEqual(result["gates"]["correctness"]["status"], "INCONCLUSIVE")

    def test_patch_digest_mismatch_fails_integrity(self):
        item = fixture()
        item["integrity_inputs"]["source_manifests"][1]["applied_patch_sha256"] = "8" * 64
        sealed(item["integrity_inputs"]["source_manifests"][1])
        sealed(item["integrity_inputs"])
        result = evaluate_bundle(item)
        self.assertIn("APPLIED_PATCH_MISMATCH", result["gates"]["integrity"]["reason_codes"])

    def test_multi_factor_intervention_is_capped_at_e1(self):
        item = fixture()
        item["intervention"]["additional_factors"] = ["thread scheduling policy"]
        item["intervention"]["multi_factor_justification"] = "Both factors are changed for this exploratory treatment"
        sealed(item["intervention"])
        result = evaluate_bundle(item)
        self.assertEqual(result["gates"]["isolation"]["status"], "FAIL")
        self.assertEqual(result["decision"]["verdict"], "EXPERIMENTALLY_SUPPORTED")
        self.assertEqual(result["decision"]["support_level"], "E1")

    def test_measurement_must_bind_to_source_manifest(self):
        item = fixture()
        item["measurement_sets"][1]["measurements"][0]["source_sha256"] = "7" * 64
        sealed(item["measurement_sets"][1])
        with self.assertRaisesRegex(ContractError, "measurement source"):
            verify_experiment_bundle(item)

    def test_source_tree_digest_tampering_is_rejected(self):
        item = fixture()
        manifest = item["integrity_inputs"]["source_manifests"][0]
        manifest["tree_sha256"] = "6" * 64
        sealed(manifest)
        sealed(item["integrity_inputs"])
        with self.assertRaisesRegex(ContractError, "source tree digest"):
            verify_experiment_bundle(item)

    def test_protected_secondary_regression_vetoes_primary_speedup(self):
        item = fixture()
        protected_b = next(value for value in item["measurement_sets"] if value["metric"] == "rss_mb" and value["arm"] == "B")
        for measurement in protected_b["measurements"]:
            measurement["value"] = 120
        sealed(protected_b)
        result = evaluate_bundle(item)
        self.assertEqual(result["statistics"]["primary"]["status"], "PASS")
        self.assertEqual(result["statistics"]["protected_secondary"]["rss_mb"]["status"], "FAIL")
        self.assertEqual(result["decision"]["verdict"], "REJECT")

    def test_missing_protected_arm_is_inconclusive(self):
        item = fixture()
        item["measurement_sets"] = [value for value in item["measurement_sets"] if not (value["metric"] == "rss_mb" and value["arm"] == "B")]
        result = evaluate_bundle(item)
        self.assertEqual(result["statistics"]["status"], "INCONCLUSIVE")
        self.assertEqual(result["decision"]["verdict"], "INCONCLUSIVE")

    def test_invalid_sample_limit_is_computed_not_structurally_rejected(self):
        item = fixture()
        treatment = next(value for value in item["measurement_sets"] if value["metric"] == "ttid_ms" and value["arm"] == "B")
        for measurement in treatment["measurements"][:2]:
            measurement["included"] = False
            measurement["exclusion_reason"] = "THERMAL"
        sealed(treatment)
        result = evaluate_bundle(item)
        self.assertEqual(result["statistics"]["status"], "INCONCLUSIVE")
        self.assertIn("INVALID_SAMPLE_LIMIT_EXCEEDED", result["statistics"]["primary"]["reason_codes"])

    def test_statistical_policy_must_precede_treatment(self):
        item = fixture(); item["statistical_policy"]["registered_at"] = "2026-01-01T00:03:01Z"; sealed(item["statistical_policy"])
        with self.assertRaisesRegex(ContractError, "statistical policy was not preregistered"):
            verify_experiment_bundle(item)

    def test_bonferroni_family_uses_all_protected_metrics(self):
        item = fixture()
        item["prediction"]["protected_secondary_metrics"].append({"metric": "energy_mj", "unit": "mJ", "regression_direction": "increase", "maximum_regression_percent": 5})
        sealed(item["prediction"])
        copies = []
        for original in [value for value in item["measurement_sets"] if value["metric"] == "rss_mb"]:
            clone = copy.deepcopy(original)
            clone["id"] = clone["id"].replace("RSS-MB", "ENERGY-MJ")
            clone["metric"] = "energy_mj"; clone["unit"] = "mJ"
            for measurement in clone["measurements"]:
                measurement["id"] = measurement["id"].replace("RSS-MB", "ENERGY-MJ")
            sealed(clone); copies.append(clone)
        item["measurement_sets"].extend(copies)
        result = evaluate_bundle(item)
        self.assertEqual(result["statistics"]["multiplicity"]["family_size"], 2)
        self.assertAlmostEqual(result["statistics"]["multiplicity"]["simultaneous_confidence_level"], 0.975)

    def test_evidence_digest_tampering_is_rejected(self):
        item = fixture()
        item["evidence_by_arm"]["B"][0]["value"] = 1
        with self.assertRaisesRegex(ContractError, "content_sha256 mismatch"):
            verify_experiment_bundle(item)

    def test_measurement_partitions_cannot_be_mixed(self):
        item = fixture(); item["measurement_sets"][1]["partition"] = "QUALIFICATION"; sealed(item["measurement_sets"][1])
        with self.assertRaisesRegex(ContractError, "partitions cannot be mixed"):
            verify_experiment_bundle(item)

    def test_environment_identity_change_is_inconclusive(self):
        item = fixture(); original = item["environments"][0]
        environments = [copy.deepcopy(original), copy.deepcopy(original)]; environments[1]["device"]["model"] = "other"
        self.assertEqual(verify_environment(environments, item["environment_policy"]).status, "INCONCLUSIVE")

    def test_environment_gate_ignores_self_asserted_validity_status(self):
        item = fixture(); item["environments"][0]["validity"]["status"] = "FAIL"
        item["environments"][0]["validity"]["reason_codes"] = ["CALLER_ASSERTION"]
        self.assertEqual(verify_environment(item["environments"], item["environment_policy"]).status, "PASS")

    def test_raw_thermal_state_overrides_self_asserted_pass(self):
        item = fixture(); item["environments"][0]["runtime"]["thermal_status"] = "SEVERE"
        self.assertEqual(verify_environment(item["environments"], item["environment_policy"]).status, "INCONCLUSIVE")

    def test_inverted_environment_api_policy_is_rejected(self):
        item = fixture(); item["environment_policy"]["api_level"] = {"minimum": 36, "maximum": 35}
        sealed(item["environment_policy"])
        with self.assertRaisesRegex(ContractError, "API policy range"):
            verify_experiment_bundle(item)

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
        call = {"schema_version": 1, "id": "TC-ONE", "run_id": "RUN-1", "requested_at": "2026-01-01T00:00:00Z", "started_at": "2026-01-01T00:00:02Z", "completed_at": "2026-01-01T00:00:03Z", "tool_id": "apply_patch", "risk": "R2", "request_sha256": SHA, "arguments": {}, "policy_decision": {"status": "ALLOW", "reason_codes": [], "approval_id": "AP-ONE"}, "status": "SUCCEEDED"}
        verify_tool_approval(call, [approval])
        call["request_sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "exact tool request"):
            verify_tool_approval(call, [approval])

    def test_approval_pending_tool_call_cannot_execute(self):
        call = {"run_id": "RUN-1", "risk": "R2", "requested_at": "2026-01-01T00:00:00Z", "request_sha256": SHA, "policy_decision": {"status": "REQUIRE_APPROVAL", "approval_id": "AP-ONE"}, "status": "SUCCEEDED"}
        with self.assertRaisesRegex(ContractError, "approval-pending"):
            verify_tool_approval(call, [])

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
