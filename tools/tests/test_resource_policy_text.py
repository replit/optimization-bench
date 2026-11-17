import unittest
import json
from pathlib import Path

from tools.update_catalog import reported_memory_policy


class MemoryPolicyTextTests(unittest.TestCase):
    def test_declared_heldout_availability_matches_lock_not_hydration(self):
        root = Path(__file__).resolve().parents[2]
        for task in (root / "tasks").iterdir():
            if not (task / "task.json").is_file():
                continue
            with self.subTest(task=task.name):
                settings = json.loads((task / "tests/judge.json").read_text())
                lock = json.loads((task / "tests/heldout.lock.json").read_text())
                self.assertEqual(settings["heldout_status"], lock["status"])

    def test_old_instruction_and_contract_promises_become_reporting_only(self):
        for old in (
            "- Peak memory may not exceed 1.25x the baseline's.",
            "- Peak memory of an invocation may not exceed 1.25x the baseline's.",
            "Exact outputs; peak RSS <= 1.25x baseline.",
        ):
            changed = reported_memory_policy(old)
            self.assertNotIn("1.25", changed)
            self.assertIn("reported", changed)
            self.assertIn("not gated", changed)
            self.assertEqual(reported_memory_policy(changed), changed)

    def test_semantic_memusage_and_hard_container_limits_are_not_rewritten(self):
        original = ("lzma_memusage() must truthfully report the encoder configuration; "
                    "memory limit: 16g; source growth <= 64 KB; exact errors.")
        self.assertEqual(reported_memory_policy(original), original)
