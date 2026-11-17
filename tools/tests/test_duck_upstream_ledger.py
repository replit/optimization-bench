import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / "tasks/duckdb-analytical/tests/upstream.py"
module = None
if path.is_file():
    spec = importlib.util.spec_from_file_location("duck_upstream", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@unittest.skipUnless(path.is_file(), "duckdb-analytical is not part of this checkout")
class DuckUpstreamLedgerTests(unittest.TestCase):
    def test_pass_records_assertion_count(self):
        self.assertEqual(module.outcome(b"All tests passed (72 assertions in 1 test case)", 0),
                         {"status": "passed", "assertions": 72})
        self.assertEqual(module.outcome(b"All tests passed (no assertions in 1 test case)", 0),
                         {"status": "passed", "assertions": 0})
        self.assertEqual(module.outcome(b"test cases: 1 | 1 passed\nassertions: - none -\n", 0),
                         {"status": "passed", "assertions": 0})

    def test_custom_sqllogic_skip_is_not_a_pass(self):
        result = module.outcome(b"All tests were skipped (total skipped 1)\n"
                                b"Skipped tests for the following reasons:\nrequire tpch: 1\n", 0)
        self.assertEqual(result, {"status": "skipped", "assertions": 0, "skip_reasons": ["require tpch: 1"]})

    def test_failure_and_unmatched_or_empty_selection_fail_closed(self):
        self.assertEqual(module.outcome(b"", 1)["status"], "failed")
        for text in (b"", b"No test cases matched", b"All tests passed (2 assertions in 2 test cases)",
                     b"test cases: 2 | 2 passed\nassertions: - none -\n",
                     b"test cases: 1 | 1 failed\nassertions: - none -\n"):
            with self.assertRaises(ValueError):
                module.outcome(text, 0)
