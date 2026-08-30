from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TASK = ROOT / "tasks" / "startup" / "cpu-001"
PUBLIC = TASK / "public-task"
HIDDEN = TASK / "private-evaluator" / "hidden-tests"
SPEC = importlib.util.spec_from_file_location(
    "materialize_hidden_correctness", ROOT / "tools" / "materialize_hidden_correctness.py"
)
assert SPEC and SPEC.loader
materializer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(materializer)


class HiddenCorrectnessMaterializerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.public = self.root / "inputs" / "public-task"
        self.hidden = self.root / "inputs" / "hidden-tests"
        shutil.copytree(PUBLIC, self.public)
        shutil.copytree(HIDDEN, self.hidden)
        self.destination = self.root / "evaluator-workspaces" / "run-001"

    def hidden_destination(self, root: Path) -> Path:
        suite = json.loads((self.hidden / "suite.json").read_text(encoding="utf-8"))
        return root / suite["files"][0]["destination"]

    def test_materializes_copy_with_hidden_overlay_only_in_destination(self):
        result = materializer.materialize(self.hidden, self.public, self.destination)

        self.assertTrue(self.hidden_destination(self.destination).is_file())
        self.assertFalse(self.hidden_destination(self.public).exists())
        self.assertEqual(result["suite_id"], "cpu-001-hidden-correctness-v1")
        self.assertEqual(result["overlay_file_count"], 1)
        self.assertRegex(result["workspace_sha256"], r"^[a-f0-9]{64}$")

    def test_existing_destination_is_never_reused(self):
        self.destination.mkdir(parents=True)
        marker = self.destination / "owned-by-another-run"
        marker.write_text("preserve\n", encoding="utf-8")
        with self.assertRaisesRegex(materializer.HiddenMaterializationError, "already exists"):
            materializer.materialize(self.hidden, self.public, self.destination)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")

    def test_destination_cannot_be_nested_in_public_or_hidden_inputs(self):
        for destination in (self.public / "private", self.hidden / "workspace"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(
                    materializer.HiddenMaterializationError, "physically disjoint"
                ):
                    materializer.materialize(self.hidden, self.public, destination)

    def test_tampered_hidden_source_fails_without_leaving_workspace(self):
        source = next((self.hidden / "src").rglob("*.kt"))
        source.write_text(source.read_text(encoding="utf-8") + "\n// tampered\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            materializer.materialize(self.hidden, self.public, self.destination)
        self.assertFalse(self.destination.exists())
        if self.destination.parent.exists():
            self.assertEqual(list(self.destination.parent.glob(".*.stage-*")), [])

    def test_symlink_in_public_input_is_rejected(self):
        link = self.public / "linked-source"
        try:
            link.symlink_to(self.public / "app")
        except OSError:
            self.skipTest("filesystem does not support symlinks")
        with self.assertRaisesRegex(materializer.HiddenMaterializationError, "contains symlink"):
            materializer.materialize(self.hidden, self.public, self.destination)


if __name__ == "__main__":
    unittest.main()
