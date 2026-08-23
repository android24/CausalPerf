import sys
import stat
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from export_public_task import ExportError, export


class PublicExporterTest(unittest.TestCase):
    def test_exports_manifest_without_private_material(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / "source"; destination = root / "out"
            source.mkdir(); (source / "task.yaml").write_text("id: public\n")
            manifest = export(source, destination)
            self.assertEqual([item["path"] for item in manifest["files"]], ["task.yaml"])
            self.assertTrue((destination / "public-manifest.json").is_file())
            self.assertEqual(destination.stat().st_mode & stat.S_IWUSR, 0)

    def test_rejects_git_objects(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / "source"; source.mkdir(); (source / ".git").mkdir()
            with self.assertRaisesRegex(ExportError, "forbidden"):
                export(source, root / "out")

    def test_rejects_private_canary(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / "source"; source.mkdir(); (source / "x.txt").write_text("CAUSALPERF_PRIVATE_CANARY_secret")
            with self.assertRaisesRegex(ExportError, "canary"):
                export(source, root / "out")

    def test_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); source = root / "source"; source.mkdir(); outside = root / "private"; outside.write_text("secret"); (source / "link").symlink_to(outside)
            with self.assertRaisesRegex(ExportError, "symlink"):
                export(source, root / "out")


if __name__ == "__main__":
    unittest.main()
