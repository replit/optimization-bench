import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from judge import behavior, differential

ROOT = Path(__file__).resolve().parents[2]


class DifferentialTests(unittest.TestCase):
    def test_prepare_receipts_are_retained_outside_strict_semantic_comparison(self):
        value = {"exit_code": 0, "result": {"bytes": "exact"}, "stderr": "", "leftovers": 0}
        left = differential.Observation(value, {"duration_ns": 10, "timed": False})
        right = differential.Observation(value, {"duration_ns": 20, "timed": False})
        self.assertEqual(left, right)
        self.assertEqual(json.dumps(left), json.dumps(right))
        for field, changed in (("exit_code", 1), ("result", {"bytes": "changed"}),
                               ("stderr", "changed"), ("leftovers", 1)):
            self.assertNotEqual(left, {**value, field: changed})
        def equal(a, b):
            self.assertNotIn("container_prepare", a)
            self.assertNotIn("receipts", b)
            return a == b
        generator = SimpleNamespace(generate=lambda rng, budget: [{}], shrink=lambda case: [], equal=equal)
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "task"
            (task / "tests").mkdir(parents=True)
            (task / "tests/judge.json").write_text(json.dumps({"differential": {
                "enabled": True, "seeds": [1], "case_budget": 1, "time_cap_seconds": 30}}))
            with patch.object(differential, "harness", return_value=generator), \
                    patch.object(differential, "execute", side_effect=[left, right]):
                result = differential.check(task, "baseline", "candidate", "image", "0", Path(temporary) / "out")
            self.assertTrue(result["passed"])
            receipts = result["cases"][0]["receipts"]
            self.assertEqual(receipts["baseline"]["container_prepare"]["duration_ns"], 10)
            self.assertEqual(receipts["candidate"]["container_prepare"]["duration_ns"], 20)

    def test_report_only_aggregation_is_seeded_and_does_not_change_default(self):
        from judge.aggregate_prototype import estimate
        value = {"cases": {"case": {"ab": {"pairs": [[20, 10]] * 5},
                                     "aa": {"pairs": [[10, 10]] * 5}}}}
        result = estimate(value, samples=100)
        self.assertEqual(result, estimate(value, samples=100))
        self.assertAlmostEqual(result["geometric"]["estimate"], 2)
        self.assertAlmostEqual(result["harmonic"]["estimate"], 2)
        self.assertFalse(result["default_changed"])
        self.assertFalse(result["claimable"])

    def test_process_difference_requires_review_and_retains_details(self):
        from judge import process_state
        with patch.object(process_state, "probe", side_effect=[
                {"gc_frozen": False, "threads": 1}, {"gc_frozen": True, "threads": 2}]):
            result = process_state.check("task", "baseline", "candidate", "image", "0-7")
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["claimable"])
        self.assertIsNone(result["speedup"])
        self.assertEqual(set(result["differences"]), {"gc_frozen", "threads"})
    def test_seeded_generators_are_repeatable_and_bounded(self):
        for name, budget in [("numpy-quantile", 12), ("sqlglot-mozilla-transpile", 12),
                             ("sqlite-distinct-plant-facets", 12),
                             ("git-history-change-summary", 1)]:
            module = differential.harness(ROOT / "tasks" / name)
            first = list(module.generate(random.Random(1729), budget))
            self.assertEqual(first, list(module.generate(random.Random(1729), budget)))
            self.assertEqual(len(first), budget)
            json.dumps(first)

    def test_mismatch_saves_seed_and_minimized_case(self):
        task = ROOT / "tasks/sqlite-distinct-plant-facets"
        generator = SimpleNamespace(
            generate=lambda rng, budget: [{"marker": "XXXX"}],
            shrink=lambda case: [{"marker": "X"}] if len(case["marker"]) > 1 else [],
            equal=lambda left, right: left == right)
        def execute(task, build, image, cpuset, case, timeout):
            return {"mutated": str(build) == "candidate"}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(differential, "harness", return_value=generator), \
             patch.object(differential, "execute", execute):
            with self.assertRaises(differential.DifferentialMismatch):
                differential.check(task, "baseline", "candidate", "image", "0-7", temporary, [1729])
            result = json.loads((Path(temporary) / "reproducer.json").read_text())
            self.assertEqual(result["seed"], 1729)
            self.assertEqual(result["case"]["marker"], "X")
            self.assertNotEqual(result["baseline"], result["candidate"])

    def test_differential_mismatch_precedes_process_state_review(self):
        report = {"passed": False, "status": "differential_mismatch"}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(differential, "check", side_effect=differential.DifferentialMismatch(report)), \
             patch("judge.behavior.process_state.check") as state:
            result = behavior.check(ROOT / "tasks/sqlglot-mozilla-transpile", "a", "b", "image", "0-7", temporary)
            self.assertEqual(result["status"], "differential_mismatch")
            self.assertFalse(result["correct"])
            self.assertIsNone(result["speedup"])
            state.assert_not_called()
