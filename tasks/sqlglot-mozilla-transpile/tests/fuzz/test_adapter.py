"""Task-owned contract controls; standard unittest, not a substitute for campaigns."""
import copy
import importlib.util
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import compare, digest, generate, make_case, precondition, shrink, SURFACES

ROOT = Path(__file__).resolve().parent
SURFACE_MAP = json.loads((ROOT / "surface.json").read_text())


def surface(symbol):
    return next(x for x in SURFACE_MAP["surfaces"] if x["id"] == symbol)


def observation(case):
    return {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {"task": case["task"], "surface_id": case["surface_id"],
                     "case_sha256": digest(case), "build_sha256": "a" * 64},
        "termination": {"kind": "return", "exit_code": 0},
        "outcome": {"kind": "ok", "value": ["SELECT 1 AS a"]},
        "effects": {}, "diagnostics": {},
    }


class AdapterTests(unittest.TestCase):
    def test_grammar_and_explicit_public_corpus_are_bounded_and_seeded(self):
        cases = list(generate(random.Random(1729), 100))
        self.assertEqual(cases, list(generate(random.Random(1729), 100)))
        self.assertEqual(len(cases), 100)
        self.assertEqual({x["surface_id"] for x in cases}, set(SURFACES))
        self.assertTrue(any(x["generator"] == "sqlglot-public-corpus-v1" for x in cases))
        for case in cases:
            self.assertTrue(precondition(case, surface(case["surface_id"]))["valid"])

    def test_malformed_sql_is_an_admitted_documented_error_domain(self):
        case = make_case("sqlglot.parse_one", "SELECT (")
        self.assertTrue(precondition(case, surface(case["surface_id"]))["valid"])

    def test_private_malformed_join_is_rejected(self):
        case = make_case("sqlglot.transpile", "SELECT 1 AS a")
        case["surface_id"] = "sqlglot.parser.Parser._implicit_unnests_to_explicit"
        case["input"] = {"origin": "manual-ast", "root": "Select", "join_this": None}
        spec = {**surface("sqlglot.transpile"), "id": case["surface_id"], "visibility": "private"}
        self.assertFalse(precondition(case, spec)["valid"])

    def test_manual_ast_cannot_masquerade_as_public_surface(self):
        case = make_case("sqlglot.expressions.Expression.copy", "SELECT 1 AS a")
        case["input"] = {"origin": "manual-ast", "sql": "SELECT 1 AS a", "join_this": None}
        self.assertFalse(precondition(case, surface(case["surface_id"]))["valid"])

    def test_exact_error_type_and_message_are_contractual(self):
        case = make_case("sqlglot.parse_one", "SELECT (")
        baseline = observation(case)
        baseline["termination"]["kind"] = "exception"
        baseline["outcome"] = {"kind": "error", "class": "sqlglot.errors.ParseError",
                               "message": "Expected ). Line 1, Col: 8.\n  SELECT \x1b[4m(\x1b[0m"}
        self.assertTrue(compare(baseline, copy.deepcopy(baseline), case)["equal"])
        changed = copy.deepcopy(baseline)
        changed["outcome"]["message"] += " "
        self.assertFalse(compare(baseline, changed, case)["equal"])
        changed = copy.deepcopy(baseline)
        changed["outcome"]["class"] = "ValueError"
        self.assertFalse(compare(baseline, changed, case)["equal"])

    def test_public_semantic_value_mutant_is_not_equal(self):
        case = make_case("sqlglot.transpile", "SELECT 1 AS a")
        baseline, candidate = observation(case), observation(case)
        candidate["outcome"]["value"] = ["SELECT 2 AS a"]
        self.assertFalse(compare(baseline, candidate, case)["equal"])

    def test_boolean_integer_and_float_categories_are_distinct(self):
        case = make_case("sqlglot.transpile", "SELECT 1 AS a")
        for a, b in ((True, 1), (1, 1.0), (False, 0)):
            baseline, candidate = observation(case), observation(case)
            baseline["outcome"]["value"] = a
            candidate["outcome"]["value"] = b
            self.assertFalse(compare(baseline, candidate, case)["equal"])

    def test_effects_are_exact_but_report_only_memory_is_not_compared(self):
        case = make_case("sqlglot.transpile", "SELECT 1 AS a")
        baseline, candidate = observation(case), observation(case)
        candidate["diagnostics"] = {"memory": {"peak": 10**12}}
        self.assertTrue(compare(baseline, candidate, case)["equal"])
        candidate["effects"]["stdout_utf8"] = "unexpected"
        self.assertFalse(compare(baseline, candidate, case)["equal"])

    def test_malformed_transport_and_wrong_case_never_pass(self):
        case = make_case("sqlglot.transpile", "SELECT 1 AS a")
        baseline, candidate = observation(case), observation(case)
        with self.assertRaises(ValueError):
            compare(baseline, {}, case)
        candidate["identity"]["case_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            compare(baseline, candidate, case)

    def test_error_fields_cannot_be_absent_in_both_arms(self):
        case = make_case("sqlglot.parse_one", "SELECT (")
        for value in ({"kind": "error"}, {"kind": "error", "class": "", "message": ""},
                      {"kind": "error", "class": "ParseError"}):
            baseline, candidate = observation(case), observation(case)
            baseline["outcome"] = candidate["outcome"] = value
            with self.assertRaises(ValueError):
                compare(baseline, candidate, case)

    def test_shrink_retains_surface_options_sequence_and_faults(self):
        case = make_case("sqlglot.parse_one", "SELECT something_long FROM table_name")
        for smaller in shrink(case):
            self.assertEqual(
                {k: v for k, v in case.items() if k != "input"},
                {k: v for k, v in smaller.items() if k != "input"})
            self.assertTrue(precondition(smaller, surface(smaller["surface_id"]))["valid"])

    def test_unreviewed_faults_and_options_do_not_run(self):
        case = make_case("sqlglot.transpile", "SELECT 1")
        case["faults"] = [{"kind": "allocator-interposition"}]
        self.assertFalse(precondition(case, surface(case["surface_id"]))["valid"])
        case["faults"] = []
        case["options"]["private_debug_flag"] = True
        self.assertFalse(precondition(case, surface(case["surface_id"]))["valid"])

    def test_coverage_decoder_is_bounded_and_public(self):
        from coverage_guide import decode
        for data in (b"", b"\x01", bytes(range(255)), b"x" * 10000, b"\xff" * 4096):
            case = decode(data)
            self.assertTrue(precondition(case, surface(case["surface_id"]))["valid"])
            self.assertLessEqual(len(case["input"]["sql"]), 4103)


if __name__ == "__main__":
    unittest.main()
