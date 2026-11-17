"""Native public-header controls, using an authenticated upstream source fixture.

Set BZIP2_HEADER_SOURCE to the unpacked pinned upstream source (or run in the
task image, where /app/bzip2 exists). BZIP2_HEADER_RECEIPTS optionally retains raw
compiler/linker observations. These are untimed compatibility tests, not scores.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.fuzz_runners import header_matrix

TASK = ROOT / "tasks/bzip2-codec"


class NativeHeaderConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ.get("BZIP2_HEADER_SOURCE", "/app/bzip2"))
        if not source.is_dir():
            if "BZIP2_HEADER_SOURCE" in os.environ:
                raise AssertionError("configured upstream source fixture is missing")
            raise unittest.SkipTest("provide the authenticated pinned Bzip2 source")
        for compiler in ("gcc", "g++", "make", "nm"):
            if shutil.which(compiler) is None:
                raise AssertionError("required native header tool unavailable: " + compiler)
        lock = json.loads((TASK / "environment/pristine-files.lock.json").read_text())
        for name, digest in lock.items():
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
                raise AssertionError("upstream fixture differs from source lock: " + name)
        cls.temporary = tempfile.TemporaryDirectory(prefix="bzip2-native-headers-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.receipts = Path(os.environ.get("BZIP2_HEADER_RECEIPTS", str(cls.root / "receipts")))
        cls.receipts.mkdir(parents=True, exist_ok=False)
        cls.baseline = cls.root / "baseline"
        shutil.copytree(source, cls.baseline / "source", ignore=shutil.ignore_patterns(".git"))
        built = subprocess.run(["make", "-j2", "libbz2.a"], cwd=cls.baseline / "source",
                               capture_output=True, timeout=120)
        (cls.receipts / "build.stdout").write_bytes(built.stdout)
        (cls.receipts / "build.stderr").write_bytes(built.stderr)
        if built.returncode:
            raise AssertionError("pinned native library build failed; inspect build.stderr")
        cls.candidate = cls.root / "candidate"
        shutil.copytree(cls.baseline, cls.candidate)
        cls.manifest = header_matrix.validate_manifest(
            json.loads((TASK / "tests/fuzz/header-manifest.json").read_text()))

    def record(self, cell, arm, suffix):
        output = self.receipts / (self._testMethodName + "-" + suffix)
        record = header_matrix.run_cell(cell, arm, output)
        (output / "record.json").write_text(json.dumps(record, indent=2) + "\n")
        return record, output

    def test_pristine_c_and_cpp_compile_link_and_execute(self):
        for cell in self.manifest["cells"]:
            with self.subTest(cell=cell["id"]):
                baseline, first = self.record(cell, self.baseline, cell["language"] + "-baseline")
                candidate, second = self.record(cell, self.candidate, cell["language"] + "-candidate")
                self.assertEqual(header_matrix.compare_cells(baseline, candidate)["attribution"],
                                 "same_success")
                for directory in (first, second):
                    result = subprocess.run([str(directory / "consumer")], capture_output=True,
                                            timeout=30)
                    (directory / "execute.json").write_text(json.dumps({
                        "exit_code": result.returncode, "stdout": result.stdout.decode(),
                        "stderr": result.stderr.decode(),
                        "scope": "untimed native header control; not qualification"}) + "\n")
                    self.assertEqual(result.returncode, 0)

    def test_cpp_linkage_regression_is_a_candidate_difference(self):
        cell = next(row for row in self.manifest["cells"] if row["language"] == "c++")
        baseline, _ = self.record(cell, self.baseline, "baseline")
        header = self.candidate / "source/bzlib.h"
        pristine = header.read_text()
        self.assertIn('extern "C" {', pristine)
        try:
            header.write_text(pristine.replace('extern "C" {', 'extern "C++" {'))
            candidate, _ = self.record(cell, self.candidate, "broken-linkage")
        finally:
            header.write_text(pristine)
        result = header_matrix.compare_cells(baseline, candidate)
        self.assertEqual(result["attribution"], "candidate_compatibility_difference")
        self.assertTrue(result["candidate_difference"])
        self.assertTrue(all(row["exit_code"] == 0 for row in candidate["compile"]))
        self.assertNotEqual(candidate["link"]["exit_code"], 0)

    def test_missing_cpp_compiler_is_infrastructure_failure_not_success(self):
        cell = next(row for row in self.manifest["cells"] if row["language"] == "c++")
        baseline, _ = self.record(cell, self.baseline, "baseline")
        original = shutil.which
        with patch.object(header_matrix.shutil, "which",
                          side_effect=lambda name: None if name == "g++" else original(name)):
            unavailable, _ = self.record(cell, self.candidate, "missing-compiler")
        self.assertEqual(unavailable["status"], "invalid_run")
        self.assertEqual(header_matrix.compare_cells(baseline, unavailable)["attribution"],
                         "invalid_run")
        self.assertEqual(unavailable["compile"], [])
        self.assertIsNone(unavailable["link"])


if __name__ == "__main__":
    unittest.main()
