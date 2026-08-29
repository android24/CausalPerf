from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TASK = ROOT / "tasks" / "startup" / "cpu-001" / "public-task"
SPEC = importlib.util.spec_from_file_location(
    "validate_gradle_wrapper", ROOT / "tools" / "validate_gradle_wrapper.py"
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class GradleWrapperValidatorTest(unittest.TestCase):
    def copy_task(self):
        directory = tempfile.TemporaryDirectory()
        task = Path(directory.name) / "public-task"
        shutil.copytree(TASK, task)
        self.addCleanup(directory.cleanup)
        return task

    def test_cpu_001_toolchain_is_fully_pinned(self):
        result = validator.validate(TASK)
        self.assertEqual(result["gradle"], "9.5.0")
        self.assertEqual(result["agp"], "9.3.0")

    def test_tampered_wrapper_jar_is_rejected(self):
        task = self.copy_task()
        with (task / "gradle" / "wrapper" / "gradle-wrapper.jar").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(validator.GradleToolchainError, "SHA-256 mismatch"):
            validator.validate(task)

    def test_changed_distribution_checksum_is_rejected(self):
        task = self.copy_task()
        properties = task / "gradle" / "wrapper" / "gradle-wrapper.properties"
        value = properties.read_text().replace(
            "553c78f50dafcd54d65b9a444649057857469edf836431389695608536d6b746",
            "0" * 64,
        )
        properties.write_text(value)
        with self.assertRaisesRegex(validator.GradleToolchainError, "distributionSha256Sum"):
            validator.validate(task)

    def test_version_catalog_drift_is_rejected(self):
        task = self.copy_task()
        catalog = task / "gradle" / "libs.versions.toml"
        catalog.write_text(catalog.read_text().replace('agp = "9.3.0"', 'agp = "9.3.1"'))
        with self.assertRaisesRegex(validator.GradleToolchainError, "version catalog differs"):
            validator.validate(task)

    def test_build_tools_declaration_drift_is_rejected(self):
        task = self.copy_task()
        build = task / "app" / "build.gradle.kts"
        build.write_text(
            build.read_text().replace(
                'buildToolsVersion = "36.0.0"',
                'buildToolsVersion = "35.0.0"',
            )
        )
        with self.assertRaisesRegex(validator.GradleToolchainError, "missing declarations"):
            validator.validate(task)

    def test_non_clean_build_command_is_rejected(self):
        task = self.copy_task()
        manifest = task / "task.yaml"
        manifest.write_text(manifest.read_text().replace('args: ["clean", ', "args: ["))
        with self.assertRaisesRegex(validator.GradleToolchainError, "not a clean build"):
            validator.validate(task)

    def test_lock_identity_must_match_public_task(self):
        task = self.copy_task()
        path = task / "toolchain.lock.json"
        lock = json.loads(path.read_text())
        lock["task_id"] = "startup-other-001"
        path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(validator.GradleToolchainError, "identities differ"):
            validator.validate(task)


if __name__ == "__main__":
    unittest.main()
