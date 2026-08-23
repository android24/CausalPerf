import json
import unittest
from pathlib import Path

import jsonschema

from causalperf_reference.artifacts import ContractError
from causalperf_reference.schema_registry import load_bundle, migrate, verify_bundle


ROOT = Path(__file__).parents[3]
BUNDLE = ROOT / "shared" / "schema-bundle.lock.json"
ARTIFACT_SCHEMA_ID = "https://causalperf.dev/schemas/artifact.schema.json"
ISOLATION_POLICY_SCHEMA_ID = "https://causalperf.dev/bench/schemas/isolation-policy.schema.json"
TASK_REPRODUCTION_SCHEMA_ID = "https://causalperf.dev/schemas/task-reproduction-package.schema.json"


class SchemaRegistryTest(unittest.TestCase):
    def test_locked_bundle_validates_against_schema(self):
        bundle_schema = json.loads((ROOT / "shared" / "schemas" / "schema-bundle.schema.json").read_text())
        jsonschema.Draft202012Validator(bundle_schema, format_checker=jsonschema.FormatChecker()).validate(load_bundle(BUNDLE))

    def test_locked_bundle_matches_every_repository_schema(self):
        verify_bundle(ROOT, load_bundle(BUNDLE))

    def test_bundle_digest_tampering_is_rejected(self):
        bundle = load_bundle(BUNDLE)
        bundle["entries"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "bundle digest"):
            verify_bundle(ROOT, bundle)

    def test_unknown_bundle_release_is_rejected(self):
        bundle = load_bundle(BUNDLE)
        bundle["bundle_version"] = "9.0.0"
        with self.assertRaisesRegex(ContractError, "unsupported schema bundle version"):
            verify_bundle(ROOT, bundle)

    def test_current_version_migration_is_idempotent_and_copies(self):
        document = {"schema_version": 1, "id": "AR-ONE"}
        migrated = migrate(document, schema_id=ARTIFACT_SCHEMA_ID, target_version=1)
        self.assertEqual(migrated, document)
        self.assertIsNot(migrated, document)

    def test_unknown_future_version_fails_closed(self):
        with self.assertRaisesRegex(ContractError, "no migration registered"):
            migrate({"schema_version": 1}, schema_id=ARTIFACT_SCHEMA_ID, target_version=2)

    def test_isolation_v1_to_v2_migration_is_pure_and_resealed(self):
        document = {"schema_version": 1, "id": "ISO-ONE", "content_sha256": "0" * 64}
        migrated = migrate(
            document, schema_id=ISOLATION_POLICY_SCHEMA_ID, target_version=2
        )
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(
            migrated["content_sha256"],
            "fa07a2fa1bc85a09e28f426c428dc1cccf1641f6c4e24e66210a1afcb2e8928c",
        )
        self.assertEqual(document["schema_version"], 1)

    def test_reproduction_v1_to_v2_binds_artifacts_to_partitions(self):
        artifact_digest = "a" * 64
        document = {
            "schema_version": 1,
            "artifacts": [{
                "kind": "A1_B_A2_MEASUREMENTS", "status": "PRESENT",
                "sha256": artifact_digest,
            }],
            "partitions": {
                name: {"status": "NOT_STARTED", "session_ids": [], "artifact_sha256s": []}
                for name in ("DEVELOPMENT", "CALIBRATION", "QUALIFICATION", "EVALUATION")
            },
        }
        migrated = migrate(
            document, schema_id=TASK_REPRODUCTION_SCHEMA_ID, target_version=2
        )
        self.assertEqual(migrated["artifacts"][0]["partition"], "CALIBRATION")
        self.assertEqual(
            migrated["partitions"]["CALIBRATION"]["artifact_sha256s"],
            [artifact_digest],
        )
        self.assertEqual(document["schema_version"], 1)

    def test_downgrade_fails_closed(self):
        with self.assertRaisesRegex(ContractError, "cannot downgrade"):
            migrate({"schema_version": 2}, schema_id=ARTIFACT_SCHEMA_ID, target_version=1)


if __name__ == "__main__":
    unittest.main()
