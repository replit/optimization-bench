"""Offline registration tests; native evidence comes from native_calibration.py."""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from judge import fuzzing_contract, fuzzing_generic

TASK = Path(__file__).resolve().parents[2]


class Registrations(unittest.TestCase):
    def setUp(self):
        self.gates, self.hooks = fuzzing_generic.task_runners(TASK)
        self.cases = importlib.import_module(self.hooks.__package__ + ".rc_cases")

    def test_all_actual_requests_are_registered(self):
        self.assertEqual(set(self.gates), {"repeat_in_process", "aliasing", "stream_faults"})
        self.assertTrue(callable(self.hooks.CLI_CASES))
        self.assertTrue(callable(self.hooks.HEADER_CONSUMERS))

    def test_library_fixtures_cover_both_loaders_and_apis(self):
        values = self.cases.cases()
        surfaces = {s["id"]: s for s in json.loads((TASK / "tests/fuzz/surface.json").read_text())["entries"]}
        self.assertEqual(len(values), 12)
        self.assertEqual(len({fuzzing_contract.case_digest(v) for v in values}), 12)
        for value in values:
            self.assertTrue(fuzzing_contract.public_case(
                value, surfaces[value["surface_id"]], self.hooks.PRECONDITION)["valid"])
        self.assertEqual({v["input"]["mode"] for v in values}, {"import", "require"})
        self.assertEqual({v["input"]["api"] for v in values}, {"minify", "minify_sync"})
        alias = values[0]
        self.assertEqual([step["cache"] for step in alias["sequence"]], ["a", "b", "b", "a", "b"])

    def test_invalid_and_unreviewed_faults_are_not_admitted(self):
        for mutate in (
            lambda v: v["input"].update(api="_run_cli"),
            lambda v: v["options"]["format"]["comments"].update(code="GUESS"),
            lambda v: v["options"]["format"]["comments"].update(after=999),
            lambda v: v["options"]["format"]["comments"].update(after=True),
            lambda v: v["sequence"].clear(),
        ):
            value = self.cases.cases()[1]
            mutate(value)
            self.assertFalse(self.hooks.PRECONDITION(value, {})["valid"])

    def test_real_cli_requests_small_large_file_and_all_faults(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.hooks.CLI_CASES({"out": directory})
            self.assertTrue(result["applicable"])
            self.assertEqual(len(result["cases"]), 3)
            for row in result["cases"]:
                self.assertTrue(fuzzing_contract.public_case(
                    row["case"], row["surface"], self.hooks.CLI_PRECONDITION)["valid"])
                self.assertEqual(row["step"]["argv"][:2],
                    ["/usr/local/bin/node", "{build}/project/bin/terser"])
                self.assertEqual((Path(row["inputs"]) / "input.js").read_text(),
                                 row["case"]["input"]["text"])
                bad = copy.deepcopy(row["case"])
                bad["input"]["text"] = ""
                self.assertFalse(self.hooks.CLI_PRECONDITION(bad, row["surface"])["valid"])
            self.assertGreater(len(result["cases"][1]["case"]["input"]["text"].encode()), 65536)
            self.assertIn("readonly_dir", result["cases"][2]["faults"])
            self.assertEqual(result["cases"][2]["required_outputs"], ["output/result.js"])

    def test_header_scope_matches_authenticated_upstream(self):
        proof = self.hooks.HEADER_CONSUMERS({})["applicability_evidence"]
        self.assertEqual(proof["file_count"], 324)
        self.assertEqual(proof["header_paths"], [])
        self.assertEqual(proof["native_paths"], [])
        surfaces = json.loads((TASK / "tests/fuzz/surface.json").read_text())
        known = {v["path"]: v["sha256"] for v in surfaces["evidence"]}
        for path, sha in proof["contract_files_sha256"].items():
            self.assertEqual(sha, known[path])

    def test_comparator_preserves_fault_and_cache_effects(self):
        value = self.cases.cases()[0]
        obs = {"format": "optimbench-fuzz-observation-v1",
               "identity": {"task": "terser-minify", "surface_id": value["surface_id"],
                            "case_sha256": fuzzing_contract.case_digest(value)},
               "termination": {"kind": "return", "exit_code": 0},
               "outcome": {"kind": "ok", "value": {"code": "x()"}},
               "effects": {"name_cache": {"a": {"vars": {}}}, "steps": []},
               "diagnostics": {}}
        for field in ("name_cache", "callback_faults"):
            other = copy.deepcopy(obs)
            other["effects"][field] = {"changed": True}
            self.assertFalse(self.hooks.COMPARE(obs, other, value)["equal"])


if __name__ == "__main__":
    unittest.main()
