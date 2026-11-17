"""Run the shared two-TU header compiler against an authenticated native build."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.fuzz_runners import header_matrix

TASK = ROOT / "tasks/sqlite-distinct-plant-facets"


class NativeHeaders(unittest.TestCase):
    def test_two_translation_units(self):
        configured = os.environ.get("SQLITE_RC_BUILD")
        if not configured:
            self.skipTest("set SQLITE_RC_BUILD to pinned native build containing source and objects")
        build = Path(configured).resolve()
        lock = json.loads((TASK / "environment/pristine-files.lock.json").read_text())
        for name, expected in lock.items():
            self.assertEqual(hashlib.sha256((build / "source" / name).read_bytes()).hexdigest(),
                             expected, name)
        manifest = json.loads((TASK / "tests/fuzz/header-manifest.json").read_text())
        header_matrix.validate_manifest(manifest)
        with tempfile.TemporaryDirectory() as temporary:
            for cell in manifest["cells"]:
                out = Path(temporary) / cell["id"]
                receipt = header_matrix.run_cell(cell, build, out)
                self.assertEqual(receipt["status"], "completed", receipt)
                self.assertTrue(all(c["exit_code"] == 0 for c in receipt["compile"]), receipt)
                self.assertEqual(receipt["link"]["exit_code"], 0, receipt)
                executable = out / "consumer"
                result = subprocess.run([str(executable)], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
