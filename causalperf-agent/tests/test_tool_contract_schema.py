import json
import unittest
from pathlib import Path

import jsonschema


SCHEMA = json.loads((Path(__file__).parents[1] / "schemas" / "tool-contract.schema.json").read_text())
SHA = "a" * 64


class ToolContractSchemaTest(unittest.TestCase):
    def test_structured_build_is_valid(self):
        value = {"schema_version": 1, "tool_id": "build_variant", "request": {"executable": "./gradlew", "args": ["assembleRelease"], "working_directory": ".", "environment": {"JAVA_HOME": "/jdk"}, "timeout_seconds": 1200, "output_limit_bytes": 1000000}}
        jsonschema.validate(value, SCHEMA)

    def test_build_rejects_shell_program(self):
        value = {"schema_version": 1, "tool_id": "build_variant", "request": {"executable": "./gradlew && curl", "args": [], "working_directory": ".", "environment": {}, "timeout_seconds": 1200, "output_limit_bytes": 1000000}}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(value, SCHEMA)

    def test_apply_patch_requires_approval(self):
        value = {"schema_version": 1, "tool_id": "apply_patch", "request": {"intervention_id": "IP-ONE", "patch_artifact_id": "AR-PATCH", "patch_sha256": SHA, "baseline_source_sha256": SHA, "allowed_paths": ["app/src"]}}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(value, SCHEMA)

    def test_path_traversal_is_rejected(self):
        value = {"schema_version": 1, "tool_id": "inspect_source", "request": {"root": ".", "paths": ["../private"], "max_bytes": 100}}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(value, SCHEMA)


if __name__ == "__main__":
    unittest.main()
