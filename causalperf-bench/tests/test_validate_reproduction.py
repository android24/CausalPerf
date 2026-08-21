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
        next(item for item in document["artifacts"] if item["kind"] == "SOURCE").update(status="MISSING", reason="test")
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
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory); (task / "reproduction.json").write_text(json.dumps(document)); (task / "SPEC.md").write_text("different")
            document["artifacts"][0]["sha256"] = validator.digest_path(task / "SPEC.md")
            (task / "reproduction.json").write_text(json.dumps(document))
            with self.assertRaisesRegex(validator.ReproductionError, "reused across partitions"):
                validator.validate_manifest(task, SCHEMA)


if __name__ == "__main__":
    unittest.main()
