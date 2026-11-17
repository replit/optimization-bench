import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from judge import policy, score
from tools import export_candidate_source


class CandidateSourceExportTests(unittest.TestCase):
    def fixture(self, root, files=False):
        task, pristine, candidate = root / "task", root / "pristine", root / "candidate"
        task.mkdir()
        (pristine / "src/tests").mkdir(parents=True)
        (pristine / "src/main.py").write_text("answer=1\n")
        (pristine / "src/tests/original.txt").write_text("original protected fixture")
        rules = {"source_roots": ["src"], "allowed_extensions": [".py"],
                 "protected_paths": ["*/tests/*"], "max_source_files": 20, "max_source_bytes": 10000}
        if files:
            rules.update(source_roots=["."], allowed_files=["src/main.py"], operations=["modify"])
        (task / "release.json").write_text(json.dumps({"source_policy": rules}))
        shutil.copytree(pristine, candidate)
        (candidate / "src/main.py").write_text("answer=2\n")
        (candidate / "src/candidate-only.so").write_bytes(b"\x7fELFnot-source")
        (candidate / ".git").mkdir()
        (candidate / ".git/config").write_text("private donor identity, never an export")
        return task, pristine, candidate, rules

    def test_directory_export_replays_source_and_original_protected_files_not_candidate_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, pristine, candidate, rules = self.fixture(root)
            for directory in (pristine, candidate):
                metadata = directory / "scorer-metadata/example/tests/heldout/manifest.json"
                metadata.parent.mkdir(parents=True)
                metadata.write_text('{"protected":"scorer only"}')
            descriptor = export_candidate_source.export(task, candidate, pristine, root / "export")
            mapping, _ = score.artifacts(task, descriptor)
            self.assertEqual(mapping, {"src": str(root / "export/source/src")})
            self.assertFalse((root / "export/source/src/candidate-only.so").exists())
            self.assertFalse((root / "export/source/.git").exists())
            self.assertFalse((root / "export/source/scorer-metadata").exists())
            self.assertEqual((root / "export/source/src/tests/original.txt").read_text(),
                             "original protected fixture")
            policy.materialize(mapping, pristine, root / "replay", rules)
            self.assertEqual((root / "replay/src/main.py").read_text(), "answer=2\n")
            policy.envelope(root / "replay", pristine, rules)

    def test_modify_only_file_export_contains_only_declared_production_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, pristine, candidate, _ = self.fixture(root, files=True)
            descriptor = export_candidate_source.export(task, candidate, pristine, root / "export")
            mapping, _ = score.artifacts(task, descriptor)
            self.assertEqual(set(mapping), {"src/main.py"})
            self.assertEqual([path.relative_to(root / "export/source").as_posix()
                              for path in (root / "export/source").rglob("*") if path.is_file()],
                             ["src/main.py"])

    def test_source_validation_still_rejects_symlinked_editable_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, pristine, candidate, _ = self.fixture(root)
            (candidate / "src/main.py").unlink()
            (candidate / "src/main.py").symlink_to(pristine / "src/main.py")
            with self.assertRaisesRegex(ValueError, "source_not_regular"):
                export_candidate_source.export(task, candidate, pristine, root / "export")

    def test_published_module_entrypoint_exports_and_replays_the_same_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, pristine, candidate, rules = self.fixture(root)
            output = subprocess.check_output(
                [sys.executable, "-m", "tools.export_candidate_source", "--task", str(task),
                 "--source", str(candidate), "--pristine", str(pristine), "--out", str(root / "export")],
                cwd=Path(export_candidate_source.__file__).resolve().parents[1], text=True).strip()
            mapping, _ = score.artifacts(task, Path(output))
            admitted = policy.artifacts_to_envelope(mapping, pristine, root / "replay", rules)
            self.assertEqual(admitted["files"]["src/main.py"], "answer=2\n")
            self.assertFalse((root / "export/source/src/candidate-only.so").exists())


if __name__ == "__main__":
    unittest.main()
