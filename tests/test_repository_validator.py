import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("validate_repository", ROOT / "tools" / "validate_repository.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class RepositoryValidatorTest(unittest.TestCase):
    def test_ownership_rejects_private_answer_in_agent(self):
        path = ROOT / "causalperf-agent" / "private-evaluator" / "ground-truth.json"
        with self.assertRaisesRegex(validator.RepositoryError, "ownership"):
            validator.validate_ownership(ROOT, [path])

    def test_generated_file_check_rejects_pyc(self):
        path = ROOT / "shared" / "reference" / "x.pyc"
        with self.assertRaisesRegex(validator.RepositoryError, "generated"):
            validator.validate_tracked_generated_files(ROOT, [path])

    def test_markdown_link_check_rejects_missing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "README.md"
            document.write_text("[missing](docs/missing.md)\n", encoding="utf-8")
            with self.assertRaisesRegex(validator.RepositoryError, "broken"):
                validator.validate_markdown_links(root, [document])

    def test_public_task_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / "causalperf-bench" / "tasks" / "x" / "public-task"
            public.mkdir(parents=True)
            outside = root / "answer"
            outside.write_text("secret", encoding="utf-8")
            (public / "link").symlink_to(outside)
            with self.assertRaisesRegex(validator.RepositoryError, "unsafe"):
                validator.validate_public_tasks(root)


if __name__ == "__main__":
    unittest.main()
