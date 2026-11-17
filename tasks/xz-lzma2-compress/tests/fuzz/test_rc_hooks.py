import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from judge.fuzzing_generic import _call_evidence, task_runners
from tools.fuzz_runners.header_matrix import validate_manifest

TASK = Path(__file__).resolve().parents[2]
_, hooks = task_runners(TASK)
driver = __import__(hooks.__package__ + ".rc_driver", fromlist=["corpus"])


class HookTests(unittest.TestCase):
    def test_registration_and_frozen_cases(self):
        self.assertEqual(set(hooks.GATES), {"stream_faults", "repeat_in_process", "aliasing"})
        self.assertEqual(len(driver.corpus()), 12)
        for case in driver.corpus():
            self.assertTrue(hooks.PRECONDITION(case, driver.SURFACE)["valid"])
        for delta in ({"task": "other"}, {"operation": True}, {"operation": 9},
                      {"input_chunk": 0}, {"output_chunk": 32769}):
            case = {**driver.corpus()[0], **delta}
            self.assertFalse(hooks.PRECONDITION(case, driver.SURFACE)["valid"])

    def test_real_cli_requests_cover_stdout_and_file_output(self):
        from judge.fuzzing_io import cli_reachable
        with tempfile.TemporaryDirectory() as temporary:
            request = hooks.CLI_CASES({"out": temporary})
            reachable = cli_reachable(request, TASK.name, hooks.CLI_PRECONDITION)
            self.assertEqual(reachable["status"], "passed", reachable)
            self.assertEqual(len(request["cases"]), 3)
            for spec in request["cases"]:
                self.assertTrue(hooks.CLI_PRECONDITION(spec["case"], spec["surface"])["valid"])
                self.assertTrue(all("{inputs}" not in arg and "{scratch}" not in arg
                                    for arg in spec["step"]["argv"]))
            fixture_root = Path(request["cases"][0]["inputs"])
            for name, size in (("small", 78), ("large", 262144)):
                import lzma
                body = (fixture_root / (name + ".xz")).read_bytes()
                self.assertEqual(len(lzma.decompress(body)), size)
            self.assertTrue((fixture_root / "out/source").is_file())

    def test_two_tu_matrix_covers_entire_public_header_inventory(self):
        manifest = json.loads((TASK / "tests/fuzz/header-manifest.json").read_text())
        validate_manifest(manifest)
        self.assertEqual({row["language"] for row in manifest["cells"]}, {"c", "c++"})
        pristine = json.loads((TASK / "environment/pristine-files.lock.json").read_text())
        inventory = pristine.get("files_sha256", pristine)
        expected = {"project/" + name for name in inventory
                    if name.startswith("src/liblzma/api/") and name.endswith(".h")}
        self.assertEqual(len(expected), 15)
        for cell in manifest["cells"]:
            self.assertEqual(set(cell["headers"]), expected)
            self.assertEqual(len(cell["translation_units"]), 2)
            self.assertEqual(cell["libraries"], ["cmake/liblzma.a"])

    @unittest.skipUnless(os.environ.get("XZ_RC_FACTS"), "Native image facts not supplied")
    def test_real_native_facts_and_rejection_controls(self):
        root = Path(os.environ["XZ_RC_FACTS"])
        context = {"task": TASK, "builds": json.loads((root / "builds.json").read_text())}
        for gate, filename in (("stream_faults", "stream-facts.json"),
                               ("repeat_in_process", "repeat-facts.json"),
                               ("aliasing", "alias-facts.json")):
            facts = json.loads((root / filename).read_text())
            result = _call_evidence(gate, facts, hooks, context)
            self.assertEqual(result["status"], "passed", result)
            changed = copy.deepcopy(facts)
            for observation in changed["cases"][0]["candidate"]:
                observation["effects"]["injected_bad_output"] = True
            self.assertEqual(_call_evidence(gate, changed, hooks, context)["status"],
                             "contract_regression")
            if gate == "repeat_in_process":
                self.assertNotEqual(facts["workers"]["baseline"]["worker_id"],
                                    facts["workers"]["candidate"]["worker_id"])
                self.assertGreaterEqual(len(facts["lifecycle"]), 24)
                changed = copy.deepcopy(facts)
                for row in changed["lifecycle"]:
                    row["candidate"]["after"]["fds"] += 1
                self.assertEqual(_call_evidence(gate, changed, hooks, context)["status"], "needs_review")

    @unittest.skipUnless(os.environ.get("XZ_RC_SOURCE"), "Authenticated source not supplied")
    def test_support_quotes_are_authenticated_upstream(self):
        source = Path(os.environ["XZ_RC_SOURCE"])
        manifest = json.loads((TASK / "tests/fuzz/header-manifest.json").read_text())
        for cell in manifest["cells"]:
            for ref in cell["support_refs"]:
                body = (source / ref["path"].removeprefix("project/")).read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), ref["sha256"])
                self.assertIn(ref["quote"], body.decode())


if __name__ == "__main__":
    unittest.main()
