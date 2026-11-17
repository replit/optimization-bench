import unittest
from judge import score_result
from judge.verifier import performance_units, measure_unit


def measured(units, aggregate):
    """Per-unit A/B CIs plus the scheduler's equal-weight aggregate CI."""
    return {"correct": True, "speedup": sum(aggregate) / 2, "quality": {"noisy": False},
            "wall_statistics": {"ci95": list(aggregate)},
            "cases": {name: {"aa": {"ci95": [.995, 1.005], "status": "resolved",
                                    "sample_count": 5, "relative_half_width": .005},
                             "ab": {"ci95": list(ab), "status": "resolved", "sample_count": 5}}
                      for name, ab in units.items()}}


class PerformanceUnitTests(unittest.TestCase):
    roles = {"a": "performance", "b": "performance", "guard": "correctness"}

    def test_default_and_valid_frozen_split(self):
        self.assertIsNone(performance_units({}, self.roles, True))
        groups = {"natural": ["b", "a"]}
        self.assertEqual(performance_units({"performance_units": {"public": groups}},
                                          self.roles, True), groups)

    def test_duplicates_omissions_correctness_and_unknown_rejected(self):
        for groups in ({"u": ["a"]}, {"u": ["a", "a", "b"]},
                       {"u": ["a"], "v": ["a", "b"]}, {"u": ["a", "b", "guard"]},
                       {"u": ["a", "b", "unknown"]}, {"guard": ["a", "b"]}, {}):
            with self.assertRaises(ValueError):
                performance_units({"performance_units": {"public": groups}}, self.roles, True)

    def test_every_component_once_in_frozen_order_and_sum_existing_intervals(self):
        calls = []
        def measure(case, build):
            calls.append((case, build))
            return {"correct": True, "ns": {"a": 300, "b": 700}[case],
                    "resources": {"peak_memory_bytes": 17, "cpu_time_usec": 3},
                    "steps": [{"exit_code": 0}]}
        result = measure_unit(["b", "a"], measure, "candidate")
        self.assertEqual(calls, [("b", "candidate"), ("a", "candidate")])
        self.assertEqual(result["ns"], 1000)
        self.assertEqual([row["case"] for row in result["components"]], ["b", "a"])
        self.assertEqual(result["resources"]["peak_memory_bytes"], 17)

    def test_one_component_mismatch_rejects_whole_unit(self):
        result = measure_unit(["a", "b"], lambda case, build: {
            "correct": case == "a", "ns": 100}, "candidate")
        self.assertFalse(result["correct"])



class UnitHeadlineTests(unittest.TestCase):
    def test_unchanged_unit_does_not_veto_aggregate_speedup(self):
        result = score_result.interpret(measured(
            {"fast": (1.9, 2.1), "same": (.996, 1.004)}, (1.38, 1.45)))
        self.assertEqual(result["status"], "speedup")
        self.assertEqual(result["optim_score"], 1.38)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["unit_notes"],
                         ["same: candidate difference is within the A/A noise floor"])
        self.assertFalse(result["cases"]["same"]["difference_clears_aa_noise"])

    def test_aggregate_inside_noise_stays_inconclusive(self):
        result = score_result.interpret(measured(
            {"a": (.996, 1.004), "b": (.997, 1.003)}, (.998, 1.002)))
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["optim_score"], 1.0)
        self.assertIn("aggregate_within_aa_noise", result["reason_codes"])

    def test_offsetting_units_do_not_score_and_aa_gates_still_veto(self):
        offset = score_result.interpret(measured({"up": (1.19, 1.21), "down": (.82, .84)},
                                                 (.99, 1.004)))
        self.assertEqual(offset["optim_score"], 1.0)
        biased = measured({"fast": (1.9, 2.1), "same": (.996, 1.004)}, (1.38, 1.45))
        biased["cases"]["same"]["aa"]["ci95"] = [1.03, 1.04]  # beyond the ±2.5% band
        self.assertEqual(score_result.interpret(biased)["status"], "inconclusive")

    def test_single_unit_keeps_per_unit_veto(self):
        result = score_result.interpret(measured({"only": (.996, 1.004)}, (1.006, 1.01)))
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("candidate_within_aa_noise", result["reason_codes"])
        self.assertEqual(result["unit_notes"], [])
