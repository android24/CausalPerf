import json
import unittest
from pathlib import Path

import jsonschema


SCHEMAS = Path(__file__).parents[2] / "schemas"
SHA = "a" * 64


def schema(name):
    return json.loads((SCHEMAS / name).read_text())


class RuntimeSchemaTest(unittest.TestCase):
    def test_statistical_policy_rejects_unsupported_design(self):
        item = {"schema_version": 1, "id": "SP-ONE", "policy_version": "1", "prediction_id": "PR-ONE", "registered_at": "2026-01-01T00:00:00Z", "design": "randomized_interleaved", "minimum_included_per_arm": 10, "max_invalid_percent": 10, "max_baseline_drift_percent": 5, "bootstrap_resamples": 1000, "confidence_level": 0.95, "seed": 1, "multiplicity": {"method": "bonferroni", "family": "protected_secondary_metrics"}, "content_sha256": SHA}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("statistical-policy.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_environment_policy_rejects_unknown_thermal_state(self):
        item = {"schema_version": 1, "id": "EP-ONE", "policy_version": "1", "api_level": {"minimum": 29, "maximum": 36}, "allowed_abis": ["x86_64"], "min_battery_percent": 50, "charging": "ANY", "allowed_thermal_statuses": ["UNKNOWN"], "expected_online_cpu_count": 8, "min_available_memory_mb": 1024, "max_background_load_percent": 5, "compilation_mode": "partial", "content_sha256": SHA}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("environment-policy.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_correctness_report_has_no_self_asserted_status(self):
        item = {"schema_version": 1, "id": "CR-ONE", "run_id": "RUN", "phase": "BASELINE", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:01Z", "suite_id": "suite", "suite_sha256": SHA, "source_manifest_id": "SM-ONE", "command_request_sha256": SHA, "exit_code": 0, "test_count": 1, "failure_count": 0, "skipped_count": 0, "result_artifact_sha256": SHA, "content_sha256": SHA, "status": "PASS"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("correctness-report.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_treatment_source_manifest_requires_patch_digest(self):
        item = {"schema_version": 1, "id": "SM-ONE", "run_id": "RUN", "role": "TREATMENT", "created_at": "2026-01-01T00:00:00Z", "entries": [{"path": "app/src/Main.kt", "sha256": SHA, "size_bytes": 1}], "tree_sha256": SHA, "content_sha256": SHA}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("source-manifest.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_integrity_input_requires_protected_paths(self):
        item = {"schema_version": 1, "id": "II-ONE", "run_id": "RUN", "created_at": "2026-01-01T00:00:00Z", "policy_sha256": SHA, "protected_paths": [], "source_manifests": [], "content_sha256": SHA}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("integrity-input.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_artifact_rejects_path_traversal(self):
        item = {"schema_version": 1, "id": "AR-ONE", "run_id": "RUN", "partition": "DEVELOPMENT", "kind": "LOG", "created_at": "2026-01-01T00:00:00Z", "producer": {"id": "test", "version": "1"}, "relative_path": "../private.txt", "media_type": "text/plain", "size_bytes": 1, "sha256": SHA, "retention": "RUN"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("artifact.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_tool_call_requires_approval_id(self):
        item = {"schema_version": 1, "id": "TC-ONE", "run_id": "RUN", "requested_at": "2026-01-01T00:00:00Z", "tool_id": "apply_patch", "risk": "R2", "request_sha256": SHA, "arguments": {}, "policy_decision": {"status": "REQUIRE_APPROVAL", "reason_codes": []}, "status": "APPROVAL_PENDING"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("tool-call.schema.json"), format_checker=jsonschema.FormatChecker())

    def test_rollback_required_decision_requires_result(self):
        item = {"schema_version": 1, "id": "ER-ONE", "run_id": "RUN", "task_id": "task", "task_version": "0.1.0", "partition": "DEVELOPMENT", "protocol_version": "1", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:01:00Z", "prediction_id": "PR-ONE", "intervention_id": "IP-ONE", "measurement_set_ids": ["MS-A1", "MS-B", "MS-A2"], "gate_result_ids": ["integrity", "correctness", "environment", "mechanism", "statistics"], "artifact_ids": ["AR-ONE"], "decision": "ROLLBACK_REQUIRED", "support_level": "NONE", "ledger_head_sha256": SHA, "content_sha256": SHA}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(item, schema("experiment-result.schema.json"), format_checker=jsonschema.FormatChecker())


if __name__ == "__main__":
    unittest.main()
