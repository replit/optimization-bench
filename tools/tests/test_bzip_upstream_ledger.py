"""Allocator invocation counts are facts, not original test identities."""
import importlib.util
import json
from pathlib import Path
import unittest

from judge.upstream import compare_ledgers

path = Path(__file__).resolve().parents[2] / "tasks/bzip2-codec/tests/upstream.py"
spec = importlib.util.spec_from_file_location("bzip_upstream", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def output(callbacks=5, semantics=50):
    names = ["semantic-" + str(i) for i in range(semantics)]
    names[23:23] = ["callback free ownership"] * callbacks
    return "".join(f"IDENTITY\t{i}\t{name}\n" for i, name in enumerate(names, 1)) + json.dumps(
        {"checks": len(names), "status": "passed"}) + "\n"


class BzipUpstreamLedgerTests(unittest.TestCase):
    def test_more_or_fewer_callback_calls_preserve_all_semantic_identities(self):
        baseline, _ = module.api_guard_outcomes(output())
        for count in (1, 2, 5, 7, 8, 25):
            with self.subTest(count=count):
                rows, facts = module.api_guard_outcomes(output(count))
                self.assertEqual(rows, baseline)
                self.assertEqual(facts["semantic_checks"], 50)
                self.assertEqual(facts["callback_ownership_checks"], count)
                self.assertEqual(facts["total_checks"], 50 + count)
                self.assertEqual(len(facts["raw_identity_rows"]), 50 + count)
                self.assertTrue(compare_ledgers({"tests": baseline}, {"tests": rows}))

    def test_missing_semantic_or_callback_coverage_still_rejects(self):
        for text in (output(semantics=49), output(semantics=51), output(callbacks=0)):
            with self.subTest(text=text[-90:]):
                with self.assertRaises(ValueError):
                    module.api_guard_outcomes(text)

    def test_incomplete_reordered_or_disagreeing_ledger_still_rejects(self):
        for text in ("", output().replace("IDENTITY\t1\t", "IDENTITY\t2\t", 1),
                     output().replace('"checks": 55', '"checks": 54'),
                     output().replace('"passed"', '"failed"'), output().rsplit("\n", 2)[0]):
            with self.subTest(text=text[-90:]):
                with self.assertRaises(ValueError):
                    module.api_guard_outcomes(text)

    def test_semantic_identity_change_still_fails_frozen_comparator(self):
        original, _ = module.api_guard_outcomes(output())
        changed, _ = module.api_guard_outcomes(output().replace("semantic-12", "different"))
        with self.assertRaisesRegex(ValueError, "identities/outcomes changed"):
            compare_ledgers({"tests": original}, {"tests": changed})


if __name__ == "__main__":
    unittest.main()
