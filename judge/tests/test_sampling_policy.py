import json
from pathlib import Path
import unittest
from unittest.mock import patch

from judge.sampling_policy import resolve
from judge import scheduler, score_result, statistics


def observation(ci=(1.044, 1.101), aa=(.9925, 1.0075), resolved=False):
    return {"correct": True, "speedup": 1.073, "quality": {"noisy": False},
            "wall_statistics": {"ci95": list(ci)},
            "cases": {"unit": {
                "ab": {"ci95": list(ci), "status": "resolved" if resolved else "not resolved",
                       "sample_count": 30},
                "aa": {"ci95": list(aa), "status": "resolved", "sample_count": 30,
                       "relative_half_width": (aa[1] - aa[0]) / 2}}}}


class SamplingPolicyTests(unittest.TestCase):
    def test_declared_five_to_fifteen_sampling_is_valid_without_overrides(self):
        settings = {"minimum_pairs": 5, "maximum_pairs": 15}
        policy = resolve(settings)
        self.assertEqual(policy["effective"], {
            "minimum_pairs": 5, "maximum_pairs": 15,
            "warmups": 1, "relative_ci_half_width": .02,
        })
        self.assertEqual(policy["overrides"], {})
        self.assertFalse(any(policy["explicit"].values()))

    def test_task_minimum_is_used_and_only_explicit_overrides_are_recorded(self):
        declared = {"minimum_pairs": 30, "maximum_pairs": 30,
                    "warmups": 1, "relative_ci_half_width": .02}
        policy = resolve(declared)
        self.assertEqual(policy["effective"], declared)
        self.assertEqual(policy["overrides"], {})
        self.assertFalse(any(policy["explicit"].values()))
        changed = resolve(declared, minimum=5)
        self.assertEqual(changed["declared"], declared)
        self.assertEqual(changed["effective"]["minimum_pairs"], 5)
        self.assertEqual(changed["overrides"], {"minimum_pairs": 5})
        self.assertTrue(changed["explicit"]["minimum_pairs"])

    def test_maximum_only_override_and_invalid_effective_bounds(self):
        self.assertEqual(resolve({"minimum_pairs": 5, "maximum_pairs": 30}, maximum=20)["effective"],
                         {"minimum_pairs": 5, "maximum_pairs": 20,
                          "warmups": 1, "relative_ci_half_width": .02})
        for minimum, maximum in ((4, 30), (30, 5), (True, 30)):
            with self.assertRaises(ValueError):
                resolve({}, minimum, maximum)

    def test_other_sampling_settings_also_come_from_task_and_record_overrides(self):
        settings = {"warmups": 3, "relative_ci_half_width": .015}
        default = resolve(settings)
        self.assertEqual(default["effective"]["warmups"], 3)
        self.assertEqual(default["effective"]["relative_ci_half_width"], .015)
        custom = resolve(settings, warmups=2, half_width=.01)
        self.assertEqual(custom["overrides"], {"warmups": 2, "relative_ci_half_width": .01})

    def test_frozen_minimum_configs_remain_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        frozen = json.loads((root / "datasets/v1.0.json").read_text())
        for name in frozen["tasks"]:
            task = root / "tasks" / name
            with self.subTest(task=task.name):
                settings = json.loads((task / "tests/judge.json").read_text())
                # Every task samples 5 to 15 pairs (#129 for numpy, then all tasks).
                self.assertEqual(settings["minimum_pairs"], 5)
                self.assertEqual(settings["maximum_pairs"], 15)


class PrecisionPolicyTests(unittest.TestCase):
    @staticmethod
    def measure(case, build):
        return {"correct": True, "ns": 100 if build == "baseline" else 50}

    def test_biased_early_aa_recentres_instead_of_throwing_away_budget(self):
        def controlled(pairs, minimum, maximum, half_width, seed, full_width_pp=None):
            n = len(pairs)
            if n < minimum:
                return {"stop": False, "status": "collecting"}
            aa = pairs[0][0] == pairs[0][1]
            ci = [.979, .995] if aa and n == minimum else [.99, 1.01] if aa else [1.98, 2.02]
            return {"stop": True, "status": "resolved", "speedup": 1 if aa else 2,
                    "ci95": ci, "relative_half_width": .01, "sample_count": n,
                    "ci95_full_width_pp": 100 * (ci[1] - ci[0]), "pairs": list(pairs)}
        with patch.object(scheduler, "stopping", side_effect=controlled):
            value = scheduler.session(["unit"], self.measure, minimum=5, maximum=8)
        self.assertEqual(value["cases"]["unit"]["aa"]["sample_count"], 6)
        self.assertTrue(value["aa_passed"])
        self.assertEqual(value["status"], "resolved")

    def test_persistent_aa_bias_uses_budget_then_stays_inconclusive(self):
        def biased(pairs, minimum, maximum, half_width, seed, full_width_pp=None):
            n = len(pairs)
            if n < minimum:
                return {"stop": False, "status": "collecting"}
            aa = pairs[0][0] == pairs[0][1]
            ci = [.960, .972] if aa else [1.98, 2.02]  # A/A offset beyond the ±2.5% band
            return {"stop": True, "status": "resolved", "speedup": 1 if aa else 2,
                    "ci95": ci, "relative_half_width": .01, "sample_count": n,
                    "ci95_full_width_pp": 100 * (ci[1] - ci[0]), "pairs": list(pairs)}
        with patch.object(scheduler, "stopping", side_effect=biased):
            value = scheduler.session(["unit"], self.measure, minimum=5, maximum=8)
        self.assertEqual(value["cases"]["unit"]["aa"]["sample_count"], 8)
        self.assertFalse(value["aa_passed"])
        value["quality"] = {"noisy": False}
        self.assertEqual(score_result.interpret(value)["status"], "inconclusive")

    def test_clean_gain_stops_early_but_noop_exhausts_ambiguity_budget(self):
        clean = scheduler.session(["unit"], self.measure, minimum=5, maximum=8)
        self.assertEqual(clean["cases"]["unit"]["ab"]["sample_count"], 5)
        noop = scheduler.session(["unit"], lambda *_: {"correct": True, "ns": 100},
                                 minimum=5, maximum=8)
        self.assertEqual(noop["cases"]["unit"]["ab"]["sample_count"], 8)
        self.assertEqual(noop["cases"]["unit"]["session_stop"]["reason"], "maximum_pairs")

    def test_task_declared_aa_bar_gates_stopping_precision_and_reasons(self):
        seen = []

        def four_pp(pairs, minimum, maximum, half_width, seed, full_width_pp=None):
            n = len(pairs)
            aa = pairs[0][0] == pairs[0][1]
            if aa:
                seen.append(full_width_pp)
            if n < minimum:
                return {"stop": False, "status": "collecting"}
            ci = [.98, 1.02] if aa else [1.98, 2.02]  # A/A full width 4 pp
            return {"stop": True, "status": "resolved", "speedup": 1 if aa else 2,
                    "ci95": ci, "relative_half_width": .02, "sample_count": n,
                    "ci95_full_width_pp": 100 * (ci[1] - ci[0]), "pairs": list(pairs)}
        # The default bar is 5 pp for every task (raised from 3 pp in review of #138);
        # a task can still declare a stricter bar.
        with patch.object(scheduler, "stopping", side_effect=four_pp):
            narrow = scheduler.session(["unit"], self.measure, minimum=5, maximum=8,
                                       aa_max_full_width_pp=3.0)
            self.assertEqual(set(seen), {3.0})
            seen.clear()
            wider = scheduler.session(["unit"], self.measure, minimum=5, maximum=8)
            self.assertEqual(set(seen), {5.0})
        self.assertEqual(narrow["aa_precision"]["maximum_full_width_pp"], 3.0)
        self.assertFalse(narrow["aa_precision"]["passed"])
        self.assertTrue(narrow["aa_precision"]["units"]["unit"]["above_bar"])
        self.assertEqual(wider["aa_precision"]["maximum_full_width_pp"], 5.0)
        self.assertTrue(wider["aa_precision"]["passed"])
        for value in (narrow, wider):
            value["quality"] = {"noisy": False}
        strict, relaxed = score_result.interpret(narrow), score_result.interpret(wider)
        self.assertIn("unit: A/A full CI width exceeds the 3 pp qualification bar", strict["reasons"])
        self.assertEqual(strict["aa_precision"]["maximum_full_width_pp"], 3.0)
        self.assertFalse(strict["aa_precision"]["passed"])
        self.assertFalse(any("qualification bar" in r for r in relaxed["reasons"]))
        self.assertEqual(relaxed["aa_precision"]["maximum_full_width_pp"], 5.0)
        self.assertTrue(relaxed["aa_precision"]["passed"])

    def test_aa_bar_must_be_finite_positive_and_bounded(self):
        for bad in (0, -1, 10.5, float("nan"), float("inf"), "5", None):
            with self.subTest(bar=bad), self.assertRaises(ValueError):
                scheduler.session(["unit"], self.measure, minimum=5, maximum=8, aa_max_full_width_pp=bad)

    def test_no_task_overrides_the_default_aa_bar(self):
        root = Path(__file__).resolve().parents[2]
        declared = {}
        for path in sorted((root / "tasks").glob("*/tests/judge.json")):
            settings = json.loads(path.read_text())
            if "aa_max_full_width_pp" in settings:
                declared[path.parents[1].name] = settings["aa_max_full_width_pp"]
        self.assertEqual(declared, {})

    def test_aa_full_width_bar_is_not_the_relative_half_width(self):
        fake = {"speedup": 1, "ci95": [.983765, 1.016235],
                "relative_half_width": .016235, "pairs": [[100, 100]] * 5, "sample_count": 5}
        with patch.object(statistics, "bootstrap", return_value=fake):
            old = statistics.stopping([(100, 100)] * 5)
            corrected = statistics.stopping([(100, 100)] * 5, maximum=10, full_width_pp=3)
        self.assertTrue(old["stop"])
        self.assertFalse(corrected["stop"])
        self.assertAlmostEqual(corrected["ci95_full_width_pp"], 3.247)
        self.assertEqual(corrected["status"], "not resolved")

    def test_significant_imprecise_sqlglot_result_scores_at_lcb(self):
        result = score_result.interpret(observation())
        self.assertEqual(result["status"], "speedup_imprecise")
        self.assertEqual(result["speedup"], 1.044)
        self.assertEqual(result["optim_score"], 1.044)
        self.assertEqual(result["point_speedup"], 1.073)
        self.assertIn("paired_precision_unmet", result["reason_codes"])
        self.assertTrue(result["aa_precision"]["passed"])

    def test_noisy_biased_or_wide_aa_cannot_become_imprecise_speedup(self):
        # Wide: a 6 pp A/A exceeds the 5 pp bar (3.247 pp now passes it).
        values = [observation(aa=(.97, 1.03)),
                  observation(aa=(1.03, 1.04)), observation()]
        values[-1]["quality"]["noisy"] = True
        for value in values:
            with self.subTest(value=value):
                result = score_result.interpret(value)
                self.assertEqual(result["status"], "inconclusive")
                self.assertIsNone(result["speedup"])
                self.assertEqual(result["optim_score"], 1)
        self.assertFalse(score_result.interpret(values[0])["aa_precision"]["passed"])

    def test_interval_not_clearing_noise_stays_inconclusive(self):
        for ci in ((.99, 1.1), (1.001, 1.006), (.9, .98)):
            with self.subTest(ci=ci):
                self.assertEqual(score_result.interpret(observation(ci=ci))["status"], "inconclusive")

    def test_aggregate_wall_bootstraps_units_without_weighting_by_duration(self):
        result = statistics.aggregate_wall({"short": [(100, 50)] * 5,
                                            "long": [(10000, 5000)] * 5})
        self.assertAlmostEqual(result["speedup"], 2)
        self.assertAlmostEqual(result["ci95"][0], 2)
        self.assertAlmostEqual(result["ci95"][1], 2)

    def test_paper_aggregation_retains_the_lcb_not_the_raw_point(self):
        result = score_result.interpret(observation())
        combined = statistics.aggregate_tasks([result])
        self.assertEqual(combined["speedup"], 1.044)
        self.assertEqual(combined["optim_score"], 1.044)
