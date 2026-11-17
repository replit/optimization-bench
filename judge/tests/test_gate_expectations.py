"""Protected upstream identities remain executable contracts, not run receipts."""
import json
from pathlib import Path
import tempfile
import unittest

from judge.gate_contracts import require_baseline


class GateExpectations(unittest.TestCase):
    def test_sqlfluff_rejects_changed_skipped_ids_even_with_identical_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests/upstream-expectations.json").write_text(json.dumps({
                "suites": {"upstream": {"skipped": 1, "collected": 1}},
                "skipped_nodeids": ["protected-test-id"]}))
            def suite(identity):
                return {"upstream": {"outcomes": {
                    "outcomes": {identity: {"setup": "skipped"}}, "collected": 1}}}
            require_baseline("sqlfluff-project-lint", root, suite("protected-test-id"))
            with self.assertRaisesRegex(ValueError, "skipped identity set"):
                require_baseline("sqlfluff-project-lint", root, suite("different-test-id"))
