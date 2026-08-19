from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "validate_task.py"
SPEC = importlib.util.spec_from_file_location("validate_task", SCRIPT)
assert SPEC and SPEC.loader
validate_task = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_task)


class AccessBoundaryTest(unittest.TestCase):
    def test_rejects_nested_writable_and_protected_paths(self) -> None:
        task = {
            "agent_access": {
                "writable_paths": ["app/src"],
                "protected_paths": ["app/src/androidTest"],
            }
        }
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_task.assert_access_boundaries(task)

    def test_accepts_disjoint_paths(self) -> None:
        task = {
            "agent_access": {
                "writable_paths": ["app/src/main"],
                "protected_paths": ["app/src/androidTest", "macrobenchmark"],
            }
        }
        validate_task.assert_access_boundaries(task)

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            validate_task.normalize_relative("../private-evaluator")


class PackageSeparationTest(unittest.TestCase):
    def test_rejects_private_material_in_public_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ground-truth.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "leaked"):
                validate_task.assert_public_package(root)

    def test_rejects_git_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            with self.assertRaisesRegex(ValueError, "leaked"):
                validate_task.assert_public_package(root)

    def test_rejects_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "private-ground-truth.txt"
            outside.write_text("private", encoding="utf-8")
            try:
                (root / "innocent.txt").symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "Symlink escapes"):
                    validate_task.assert_public_package(root)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
