import json
from pathlib import Path
import tempfile
import unittest

from judge.output_contract import DEFAULT, load_output_comparator

ROOT = Path(__file__).resolve().parents[2]


class OutputDeclarationTests(unittest.TestCase):
    def test_absent_declaration_is_exact_and_does_not_guess_from_json_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "tests").mkdir()
            (task / "tests/judge.json").write_text("{}")
            self.assertEqual(load_output_comparator(task), DEFAULT)

    def test_packaged_tasks_keep_their_original_stream_comparators(self):
        original = {
            "serde-json-production-crate-index-ingestion":
                {**DEFAULT, "stdout": {"kind": "json-structural"}},
        }
        for task in sorted((ROOT / "tasks").iterdir()):
            with self.subTest(task=task.name):
                self.assertEqual(load_output_comparator(task), original.get(task.name, DEFAULT))

    def test_undeclared_numeric_tolerance_and_unknown_comparators_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "tests").mkdir()
            for rule in ({"kind": "numeric"}, {"kind": "approximate"},
                         {"kind": "numeric", "rtol": True, "atol": 0}):
                value = {**DEFAULT, "stdout": rule}
                (task / "tests/judge.json").write_text(json.dumps({"output_comparator": value}))
                with self.assertRaises(ValueError):
                    load_output_comparator(task)
