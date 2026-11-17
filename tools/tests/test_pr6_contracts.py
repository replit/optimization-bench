"""The packaging conversion must not edit source/input pins or production policies."""
import hashlib
import gzip
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OriginalContractsTests(unittest.TestCase):
    def test_original_locked_inputs_and_pins_are_unchanged(self):
        fixture = json.loads((ROOT / "tools/tests/fixtures/input-contracts.json").read_text())
        contracts = fixture["locked_files"]
        for task_id, original in contracts.items():
            task = ROOT / "tasks" / task_id
            for name, digest in original.items():
                with self.subTest(task=task_id, file=name):
                    raw = (task / name).read_bytes()
                    promotions = fixture["catalog_distribution_promotions"].get(task_id, {}).get(name)
                    if promotions:
                        payload = json.loads(raw)
                        promoted = set(promotions)
                        observed = set()
                        for item in payload["files"]:
                            if item["name"] in promoted:
                                self.assertEqual(item["distribution"], "git")
                                item["distribution"] = "operator"
                                observed.add(item["name"])
                        self.assertEqual(observed, promoted)
                        # Undo only reviewed delivery labels before checking original bytes.
                        raw = (json.dumps(payload, indent=2) + "\n").encode()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            status = json.loads((task / "qualification-status.json").read_text())
            self.assertFalse(status["harbor_execution_qualified"])
            if status["scored_qualification"]:
                # Packaging froze the input/source contracts, not a permanent
                # prohibition on subsequently recording task qualification.
                self.assertEqual(status["qualification_mode"], "provisional-unregistered-host")
                self.assertFalse(status["claimable"])
                receipt = (task / status["qualification_receipt"]).resolve()
                self.assertTrue(receipt.is_relative_to(task.resolve()))
                summary = json.loads(receipt.read_text())
                self.assertEqual(summary["task"], task_id)
                self.assertFalse(summary["claimable"])
                self.assertIsNone(summary["candidate_verdict"])
                for split in ("public", "heldout"):
                    with gzip.open(receipt.parent / f"aa-{split}.json.gz", "rt") as stream:
                        aa = json.load(stream)
                    self.assertTrue(aa["passed"])
                    self.assertFalse(aa["claimable"])
            else:
                self.assertFalse(status["scored_qualification"])
            settings = json.loads((task / "tests/judge.json").read_text())
            if status["scored_qualification"]:
                self.assertFalse(settings.get("public_dry_run_only", False))
            self.assertFalse(settings["claimable"])


if __name__ == "__main__":
    unittest.main()
