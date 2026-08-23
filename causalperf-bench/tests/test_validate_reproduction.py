import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("validate_reproduction", ROOT / "tools" / "validate_reproduction.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
SCHEMA = json.loads((ROOT / "schemas" / "task-reproduction-package.schema.json").read_text())


class ReproductionValidatorTest(unittest.TestCase):
    def test_all_five_manifests_are_honest_and_valid(self):
        tasks = sorted((ROOT / "tasks" / "startup").glob("*-001"))
        results = [validator.validate_manifest(task, SCHEMA) for task in tasks]
        self.assertEqual(len(results), 5)
        self.assertEqual({item["lifecycle"] for item in results}, {"DRAFT", "IMPLEMENTED"})

    def test_implemented_package_cannot_claim_missing_source(self):
        source = ROOT / "tasks" / "startup" / "cpu-001" / "reproduction.json"
        document = json.loads(source.read_text())
        source_artifact = next(item for item in document["artifacts"] if item["kind"] == "SOURCE")
        source_artifact.update(status="MISSING", reason="test")
        source_artifact.pop("relative_path")
        source_digest = source_artifact.pop("sha256")
        document["partitions"]["DEVELOPMENT"]["artifact_sha256s"].remove(source_digest)
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "cpu-001"
            shutil.copytree(source.parent, task)
            (task / "reproduction.json").write_text(json.dumps(document))
            with self.assertRaisesRegex(validator.ReproductionError, "incomplete"):
                validator.validate_manifest(task, SCHEMA)

    def test_partition_reuse_is_rejected(self):
        document = json.loads((ROOT / "tasks" / "startup" / "io-001" / "reproduction.json").read_text())
        digest = "a" * 64
        document["partitions"]["CALIBRATION"]["artifact_sha256s"] = [digest]
        document["partitions"]["QUALIFICATION"]["artifact_sha256s"] = [digest]
        document["partitions"]["CALIBRATION"]["status"] = "OPEN"
        document["partitions"]["QUALIFICATION"]["status"] = "OPEN"
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory); (task / "reproduction.json").write_text(json.dumps(document)); (task / "SPEC.md").write_text("different")
            document["artifacts"][0]["sha256"] = validator.digest_path(task / "SPEC.md")
            document["partitions"]["DEVELOPMENT"]["artifact_sha256s"] = [document["artifacts"][0]["sha256"]]
            (task / "reproduction.json").write_text(json.dumps(document))
            with self.assertRaisesRegex(validator.ReproductionError, "reused across partitions"):
                validator.validate_manifest(task, SCHEMA)

    def test_cpu_is_implemented_but_not_calibration_complete(self):
        task = ROOT / "tasks" / "startup" / "cpu-001"
        result = validator.validate_manifest(task, SCHEMA, require_lifecycle="IMPLEMENTED")
        self.assertEqual(result["required_lifecycle"], "IMPLEMENTED")
        with self.assertRaisesRegex(validator.ReproductionError, "CALIBRATED package incomplete"):
            validator.validate_manifest(task, SCHEMA, require_lifecycle="CALIBRATED")

    def test_draft_task_cannot_pass_implemented_target(self):
        task = ROOT / "tasks" / "startup" / "io-001"
        with self.assertRaisesRegex(validator.ReproductionError, "IMPLEMENTED package incomplete"):
            validator.validate_manifest(task, SCHEMA, require_lifecycle="IMPLEMENTED")

    def test_same_artifact_kind_is_partition_scoped(self):
        task = ROOT / "tasks" / "startup" / "cpu-001"
        document = json.loads((task / "reproduction.json").read_text())
        snapshots = [
            artifact for artifact in document["artifacts"]
            if artifact["kind"] == "ENVIRONMENT_SNAPSHOT"
        ]
        self.assertEqual(
            {artifact["partition"] for artifact in snapshots},
            {"CALIBRATION", "QUALIFICATION"},
        )
        validator.validate_manifest(task, SCHEMA)

    def test_qualification_requires_fresh_experiment_evidence(self):
        required = validator.required_artifacts("QUALIFIED")
        self.assertIn("A1_B_A2_MEASUREMENTS", required["CALIBRATION"])
        self.assertIn("A1_B_A2_MEASUREMENTS", required["QUALIFICATION"])
        self.assertIn("INDEPENDENT_REPLAY", required["QUALIFICATION"])

    def test_present_artifact_must_be_bound_to_partition_registry(self):
        source = ROOT / "tasks" / "startup" / "cpu-001"
        document = json.loads((source / "reproduction.json").read_text())
        document["partitions"]["DEVELOPMENT"]["artifact_sha256s"].pop()
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "cpu-001"
            shutil.copytree(source, task)
            (task / "reproduction.json").write_text(json.dumps(document))
            with self.assertRaisesRegex(validator.ReproductionError, "absent from partition registry"):
                validator.validate_manifest(task, SCHEMA)

    def test_legacy_v1_draft_migrates_conservatively(self):
        source = ROOT / "tasks" / "startup" / "io-001"
        document = json.loads((source / "reproduction.json").read_text())
        document["schema_version"] = 1
        for artifact in document["artifacts"]:
            artifact.pop("partition")
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            shutil.copy(source / "SPEC.md", task / "SPEC.md")
            (task / "reproduction.json").write_text(json.dumps(document))
            result = validator.validate_manifest(task, SCHEMA)
            self.assertEqual(result["lifecycle"], "DRAFT")


if __name__ == "__main__":
    unittest.main()
