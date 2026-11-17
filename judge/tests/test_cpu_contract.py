import json
from pathlib import Path
import unittest

from judge.cpu_policy import CORRECTNESS_CPU_COUNTS, correctness_receipt, release_policy
from tools.update_catalog import neutral_prompt_tasks, validate_neutral_prompt

ROOT = Path(__file__).resolve().parents[2]


class CpuContractTests(unittest.TestCase):
    def test_unexecuted_or_unverified_subset_is_not_a_passing_gate(self):
        good = {"timing_performed": False, "records": [
            {"cpu_count": count, "actual_cpus": list(range(count)), "passed": True,
             "affinity_verified": True, "baseline_cases": 1, "candidate_cases": 1}
            for count in CORRECTNESS_CPU_COUNTS]}
        self.assertTrue(correctness_receipt(good)["passed"])
        self.assertFalse(correctness_receipt({})["passed"])
        good["records"][0]["passed"] = False
        self.assertFalse(correctness_receipt(good)["passed"])
        good["records"][0]["passed"] = True
        good["records"][1]["actual_cpus"] = [0]
        self.assertFalse(correctness_receipt(good)["passed"])

    def test_release_and_instruction_cpu_promises_match_the_runtime_contract(self):
        for task in sorted((ROOT / "tasks").iterdir()):
            if not (task / "task.json").is_file():
                continue
            with self.subTest(task=task.name):
                release = json.loads((task / "release.json").read_text())
                self.assertEqual(release["cpu_policy"], release_policy())
                if "cores" in release:
                    self.assertEqual(release["cores"], [8])
                text = (task / "instruction.md").read_text()
                if task.name in neutral_prompt_tasks():
                    # Prompt assertions only: release/runtime checks above
                    # remain unchanged. The reviewed neutral template does
                    # not promise a particular CPU correctness matrix.
                    validate_neutral_prompt(task)
                    self.assertNotIn("Correctness is gated at 1/2/4/8 CPUs.", text)
                else:
                    self.assertIn("Correctness is gated at 1/2/4/8 CPUs.", text)
                    self.assertIn("not a regression gate", text)
                self.assertNotIn("1/2/4-core regression checks belong", text)
