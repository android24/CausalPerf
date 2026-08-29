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
    "validate_hidden_correctness", ROOT / "tools" / "validate_hidden_correctness.py"
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class HiddenCorrectnessValidatorTest(unittest.TestCase):
    def copy_inputs(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        public = root / "public-task"
        hidden = root / "hidden-tests"
        shutil.copytree(PUBLIC, public)
        shutil.copytree(HIDDEN, hidden)
        self.addCleanup(directory.cleanup)
        return hidden, public

    def mutate_manifest(self, hidden: Path, mutation) -> None:
        path = hidden / "suite.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        value["content_sha256"] = validator.canonical_digest(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_real_hidden_suite_is_valid_and_private(self):
        result = validator.validate(HIDDEN, PUBLIC)
        self.assertEqual(result["suite_id"], "cpu-001-hidden-correctness-v1")
        self.assertEqual(result["files"], 1)

    def test_hidden_source_digest_drift_is_rejected(self):
        hidden, public = self.copy_inputs()
        source = next((hidden / "src").rglob("*.kt"))
        source.write_text(source.read_text() + "\n// changed\n", encoding="utf-8")
        with self.assertRaisesRegex(validator.HiddenCorrectnessError, "digest mismatch"):
            validator.validate(hidden, public)

    def test_public_and_private_task_identity_must_match(self):
        hidden, public = self.copy_inputs()
        self.mutate_manifest(hidden, lambda value: value.update(task_version="0.2.0"))
        with self.assertRaisesRegex(validator.HiddenCorrectnessError, "identities differ"):
            validator.validate(hidden, public)

    def test_hidden_suite_must_use_sealed_public_command(self):
        hidden, public = self.copy_inputs()
        self.mutate_manifest(
            hidden,
            lambda value: value["command"].update(args=[":app:test"]),
        )
        with self.assertRaisesRegex(validator.HiddenCorrectnessError, "sealed public correctness"):
            validator.validate(hidden, public)

    def test_overlay_cannot_replace_a_public_test(self):
        hidden, public = self.copy_inputs()
        self.mutate_manifest(
            hidden,
            lambda value: value["files"][0].update(
                destination="app/src/androidTest/java/dev/causalperf/startup/cpu/StartupCorrectnessTest.kt"
            ),
        )
        with self.assertRaisesRegex(validator.HiddenCorrectnessError, "replace a public file"):
            validator.validate(hidden, public)

    def test_application_source_cannot_detect_evaluator_context(self):
        hidden, public = self.copy_inputs()
        source = public / "app" / "src" / "main" / "java" / "Detection.kt"
        source.write_text("val args = InstrumentationRegistry.getArguments()\n", encoding="utf-8")
        with self.assertRaisesRegex(validator.HiddenCorrectnessError, "detection tokens"):
            validator.validate(hidden, public)


if __name__ == "__main__":
    unittest.main()
