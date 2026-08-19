import json
import unittest
from pathlib import Path

import jsonschema


SCHEMAS = Path(__file__).parents[2] / "schemas"
SHA = "a" * 64


def schema(name):
    return json.loads((SCHEMAS / name).read_text())


class RuntimeSchemaTest(unittest.TestCase):
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
