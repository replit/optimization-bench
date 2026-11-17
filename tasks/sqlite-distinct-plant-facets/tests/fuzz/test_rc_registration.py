"""Static registration and native fact-policy regression tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools/runner"))
from judge import fuzzing_generic
from tools.fuzz_runners.header_matrix import validate_manifest

TASK = ROOT / "tasks/sqlite-distinct-plant-facets"


class Registration(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.module = fuzzing_generic.task_runners(TASK)

    def test_callbacks(self):
        self.assertEqual(set(self.callbacks), {"stream_faults", "repeat_in_process", "aliasing"})
        self.assertTrue(callable(self.module.CLI_CASES))
        self.assertTrue(callable(self.module.HEADER_CONSUMERS))

    def test_domains(self):
        from importlib import import_module
        driver = import_module(self.module.__package__ + ".rc_driver")
        for case in driver.cases() + [driver.make_case("alias")]:
            self.assertTrue(self.module.PRECONDITION(case, {})["valid"])
            for changes in ({"task": "other"}, {"surface_id": "private"}, {"seed": 999},
                            {"fault": "unknown"}, {"mmap": True}, {"operation": "private"}):
                self.assertFalse(self.module.PRECONDITION({**case, **changes}, {})["valid"])

    def test_header_manifest(self):
        manifest = json.loads((TASK / "tests/fuzz/header-manifest.json").read_text())
        validate_manifest(manifest)
        self.assertEqual({h for c in manifest["cells"] for h in c["headers"]},
                         {"objects/sqlite3.h", "objects/sqlite3ext.h"})
        self.assertTrue(all(len(c["translation_units"]) == 2 for c in manifest["cells"]))
        self.assertTrue(all(c["libraries"] == ["objects/libsqlite3.a"] for c in manifest["cells"]))

    def test_cli_native(self):
        build = os.environ.get("SQLITE_RC_BUILD")
        if not build:
            self.skipTest("set SQLITE_RC_BUILD to a matching native build")
        with tempfile.TemporaryDirectory() as tmp:
            request = self.module.CLI_CASES({"out": Path(tmp)})
            sizes = {}
            for spec in request["cases"]:
                tree = Path(spec["inputs"])
                before = (tree / "input.db").read_bytes()
                argv = [a.replace("{build}", str(Path(build).resolve())) for a in spec["step"]["argv"]]
                result = subprocess.run(argv, cwd=tree, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                sizes[spec["case"]["mode"]] = len(result.stdout)
                self.assertEqual(before, (tree / "input.db").read_bytes())
                if spec["case"]["mode"] == "file":
                    self.assertGreater((tree / "out/result.txt").stat().st_size, 0)
            self.assertTrue(0 < sizes["small"] <= 4096)
            self.assertGreaterEqual(sizes["large"], 65536)

    def test_native_facts_controller(self):
        directory = os.environ.get("SQLITE_RC_FACTS")
        if not directory:
            self.skipTest("set SQLITE_RC_FACTS to driver pristine-vs-pristine output")
        for name, file in (("stream_faults", "stream"), ("repeat_in_process", "repeat"), ("aliasing", "alias")):
            raw = json.loads((Path(directory) / (file + "-facts.json")).read_text())
            builds = {arm: raw["cases"][0][arm][0]["identity"]["build_sha256"]
                      for arm in ("baseline", "candidate")}
            result = fuzzing_generic._call_evidence(name, raw, self.module, {"builds": builds})
            self.assertEqual(result["status"], "passed")
            if name == "stream_faults":
                for row in raw["cases"]:
                    case, obs = row["case"], row["baseline"][0]
                    # mmap off's no-page case is the paired healthy control.
                    if case["fault"] != "healthy" and not (case["fault"] == "no_page" and
                            (not case["mmap"] or case["operation"] == "upsert")):
                        self.assertGreater(obs["diagnostics"]["hits"], 0, case)
                    self.assertEqual(obs["effects"]["integrity"],
                                     [[{"type": 3, "value": "ok"}]], case)
                fallback = [r for r in raw["cases"] if r["case"]["operation"] == "facets"
                            and r["case"]["mmap"] and r["case"]["fault"] == "sys_eio"]
                self.assertTrue(all(r["baseline"][0]["diagnostics"]["fetches"] > 0 for r in fallback))


if __name__ == "__main__":
    unittest.main()
