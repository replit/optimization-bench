import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import fuzzing_generic, fuzzing_contract

TASK = Path(__file__).resolve().parents[2] / "tasks/anydoc-markdown"


@unittest.skipUnless(TASK.is_dir(), "anydoc-markdown is not part of this checkout")
class AnyDocRcTests(unittest.TestCase):
    def setUp(self):
        self.runners, self.hooks = fuzzing_generic.task_runners(TASK)

    def test_aliasing_is_registered_with_actual_facts_not_a_waiver(self):
        self.assertEqual(set(self.runners), {"repeat_in_process", "stream_faults", "aliasing"})
        value = {"format": "judge-generic-call-evidence-v1", "applicable": True}
        with patch.object(self.hooks, "_facts", return_value=value) as facts:
            self.assertIs(self.hooks.aliasing({}), value)
        facts.assert_called_once_with({}, "alias-facts.json")

    def test_alias_cases_are_public_and_distinct_from_shallow_model_probes(self):
        adapter = self.hooks.PRECONDITION.__globals__
        rows = adapter["cases"]()
        alias = [row for row in rows if row["options"]["operation"] == "alias"]
        self.assertEqual(len(alias), 2)
        for case in alias:
            self.assertTrue(fuzzing_contract.public_case(
                case, adapter["surface"](case), self.hooks.PRECONDITION)["valid"])
            self.assertIn("Document::clone", case["surface_id"])
            self.assertFalse(case["faults"])

    def test_cli_uses_real_public_node_entrypoint_and_watches_file_effects(self):
        with tempfile.TemporaryDirectory() as output:
            request = self.hooks.CLI_CASES({"out": output})
            self.assertTrue(request["applicable"])
            self.assertEqual(len(request["cases"]), 3)
            for spec in request["cases"]:
                case = spec["case"]
                self.assertTrue(fuzzing_contract.public_case(
                    case, spec["surface"], self.hooks.CLI_PRECONDITION)["valid"])
                self.assertEqual(spec["step"]["argv"][:2],
                                 ["/usr/local/bin/node", "{build}/cli/node/cli.js"])
                self.assertTrue({"healthy", "dev_full", "closed_reader", "closed_stdout"} <= set(spec["faults"]))
                self.assertEqual(spec["watch_inputs"], ["input.csv"])
            file_spec = request["cases"][2]
            self.assertEqual(file_spec["required_outputs"], ["output/result.md"])
            self.assertIn("readonly_dir", file_spec["faults"])
            self.assertGreater(len(request["cases"][1]["case"]["input"]["text"].encode()), 65536)

    def test_cli_rejects_changed_inputs_or_unknown_operation(self):
        with tempfile.TemporaryDirectory() as output:
            spec = self.hooks.CLI_CASES({"out": output})["cases"][0]
            changed = copy.deepcopy(spec["case"])
            changed["input"]["text"] = "different"
            self.assertFalse(self.hooks.CLI_PRECONDITION(changed, spec["surface"])["valid"])
            changed["options"]["mode"] = "unknown"
            self.assertFalse(self.hooks.CLI_PRECONDITION(changed, spec["surface"])["valid"])

    def test_central_alias_gate_rejects_changed_clone_observations(self):
        adapter = self.hooks.PRECONDITION.__globals__
        case = next(c for c in adapter["cases"]() if c["options"]["operation"] == "alias")
        builds = {"baseline": "a" * 64, "candidate": "b" * 64}
        row = {"case": case, "surface": adapter["surface"](case)}
        for arm in builds:
            observation = {"format": "optimbench-fuzz-observation-v1",
                           "identity": {"task": "anydoc-markdown", "surface_id": case["surface_id"],
                                        "case_sha256": case["case_sha256"], "build_sha256": builds[arm]},
                           "termination": {"kind": "return", "exit_code": 0},
                           "outcome": {"kind": "ok", "value": "before:original;after:original"},
                           "effects": {}, "diagnostics": {}}
            row[arm] = [copy.deepcopy(observation) for _ in range(2)]
        facts = {"format": "judge-generic-call-evidence-v1", "applicable": True, "cases": [row]}
        self.assertEqual(fuzzing_generic._call_evidence(
            "aliasing", facts, self.hooks, {"builds": builds})["status"], "passed")
        for observation in row["candidate"]:
            observation["outcome"]["value"] = "before:original;after:corrupted"
        self.assertEqual(fuzzing_generic._call_evidence(
            "aliasing", facts, self.hooks, {"builds": builds})["status"], "contract_regression")
