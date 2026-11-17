"""rc.6: family-wise (Bonferroni) correction for the per-unit A/A bias decision only."""
import math
import random
import unittest

from judge import scheduler, score_result, statistics
from tools.judge_control import _aa_bias_interval, _aa_centred, control_errors
from tools.tests.test_judge_control import TASK_UNITS, result_for


def bias_test(interval, k):
    alpha = .05 / k
    return {"method": "bonferroni", "family_alpha": .05, "family_size": k, "per_unit_alpha": alpha,
            "interval_level": 1 - alpha, "interval": interval, "biased": not interval[0] <= 1 <= interval[1]}


def control(task, aa_overrides):
    """A pristine control measurement for task with per-unit A/A overrides."""
    units = TASK_UNITS[task]
    k = len(units)
    cases = {}
    for name in units:
        aa = {"status": "resolved", "ci95": [.997, 1.003], "sample_count": 30,
              "relative_half_width": .003, "bias_test": bias_test([.996, 1.004], k)}
        aa.update(aa_overrides.get(name, {}))
        cases[name] = {"aa": aa, "ab": {"status": "unresolved", "ci95": [.99, 1.01],
                                        "sample_count": 30, "relative_half_width": .01}}
    return {"correct": True, "quality": {"noisy": False}, "speedup": 1,
            "wall_statistics": {"ci95": [.9993, 1.0005]}, "cases": cases}


class DecisionTests(unittest.TestCase):
    GIT = "git-history-change-summary"
    SQLGLOT = "sqlglot-mozilla-transpile"

    def check(self, task, measurement):
        result = result_for(measurement)
        return result, control_errors(result, measurement, TASK_UNITS[task])

    def test_unit_just_missing_at_95_but_inside_bonferroni_interval_passes(self):
        # Real AMD c4d rc.5 git control: patch-histogram A/A 95% CI [1.0001, 1.0020].
        # Its k=7 Bonferroni (99.29%) interval from the same bootstrap contains 1.
        measurement = control(self.GIT, {"patch-histogram": {
            "ci95": [1.0001, 1.0020], "bias_test": bias_test([.9996, 1.0026], 7)}})
        result, errors = self.check(self.GIT, measurement)
        self.assertEqual(errors, [])
        self.assertNotIn("aa_unresolved_or_biased", result["reason_codes"])
        floor = result["aa_noise_floor"]["patch-histogram"]
        self.assertEqual(floor["bias_test"]["interval"], [.9996, 1.0026])
        self.assertAlmostEqual(floor["bias_test"]["interval_level"], 1 - .05 / 7)
        self.assertFalse(floor["bias_test"]["biased"])
        # The A/A noise envelope still uses the unadjusted 95% CI.
        self.assertEqual(floor["ci95"], [1.0001, 1.0020])
        self.assertAlmostEqual(floor["maximum_log_distance_from_one"], math.log(1.0020))
        self.assertEqual(result["aa_bias_test"]["family_size"], 7)
        self.assertAlmostEqual(result["aa_bias_test"]["per_unit_alpha"], .05 / 7)
        self.assertEqual(result["aa_bias_test"]["biased_units"], [])

    def test_small_offset_inside_the_equivalence_band_passes(self):
        # sqlglot lineage on AMD: 95% CI [0.9936, 0.9988] (-0.38%). Its k=4 (98.75%)
        # interval [0.9927, 0.9997] excludes 1, but the offset lies inside the ±2.5%
        # practical-equivalence band, so the unit is centred.
        measurement = control(self.SQLGLOT, {"lineage": {
            "ci95": [.9936, .9988], "bias_test": bias_test([.9927, .9997], 4)}})
        result, errors = self.check(self.SQLGLOT, measurement)
        self.assertEqual(errors, [])
        self.assertNotIn("aa_unresolved_or_biased", result["reason_codes"])
        self.assertEqual(result["aa_noise_floor"]["lineage"]["bias_test"]["equivalence_band"], [.975, 1.025])

    def test_band_edges(self):
        for ci, centred in (([.975, .98], True), ([1.02, 1.025], True),
                            ([.9749, .98], False), ([1.02, 1.0251], False)):
            with self.subTest(ci=ci):
                aa = {"ci95": ci, "bias_test": bias_test([ci[0] - .001, ci[1] + .001], 4)}
                self.assertIs(score_result.aa_centred(aa, 4), centred)

    def test_clearly_biased_unit_still_fails(self):
        # A -2.8% A/A offset: outside the ±2.5% band, and its k=4 interval excludes 1.
        measurement = control(self.SQLGLOT, {"lineage": {
            "ci95": [.9700, .9745], "bias_test": bias_test([.9690, .9755], 4)}})
        result, errors = self.check(self.SQLGLOT, measurement)
        self.assertIn("aa_unresolved_or_biased", result["reason_codes"])
        self.assertIn("lineage: A/A unresolved, biased or malformed", errors)
        self.assertEqual(result["aa_bias_test"]["biased_units"], ["lineage"])

    def test_missing_or_inconsistent_bias_record_falls_back_to_the_stricter_95_ci(self):
        # Outside the equivalence band, so only a valid family-wise record can centre it.
        wide = bias_test([.9996, 1.0320], 7)
        for bad in (None, {**wide, "family_size": 4}, {**wide, "per_unit_alpha": .05},
                    {**wide, "method": "none"}, {**wide, "interval": [1.0262, 1.0280]},
                    {**wide, "interval": [float("nan"), 1.1]}):
            with self.subTest(bad=bad):
                aa = {"ci95": [1.0262, 1.0290], **({} if bad is None else {"bias_test": bad})}
                measurement = control(self.GIT, {"patch-histogram": aa})
                if bad is None:
                    del measurement["cases"]["patch-histogram"]["aa"]["bias_test"]
                result, errors = self.check(self.GIT, measurement)
                self.assertIn("aa_unresolved_or_biased", result["reason_codes"])
                self.assertTrue(errors)

    def test_reduced_bias_record_must_match_raw(self):
        measurement = control(self.GIT, {})
        result = result_for(measurement)
        result["aa_noise_floor"]["merge-file"]["bias_test"]["interval"] = [.9, 1.1]
        self.assertIn("merge-file: raw/reduced statistics disagree",
                      control_errors(result, measurement, TASK_UNITS[self.GIT]))

    def test_validator_copy_matches_the_judge_decision(self):
        rng = random.Random(5)
        for _ in range(200):
            k = rng.choice([1, 4, 7])
            low = rng.uniform(.99, 1.01)
            ci = [low, low + rng.uniform(0, .01)]
            spread = rng.uniform(-.001, .003)
            test = bias_test([ci[0] - spread, ci[1] + spread], rng.choice([k, k + 1]))
            aa = {"ci95": ci, "bias_test": test}
            self.assertEqual(_aa_bias_interval(aa, k), score_result.aa_bias_interval(aa, k)[0])
            self.assertEqual(_aa_centred(aa, k), score_result.aa_centred(aa, k))


class SchedulerTests(unittest.TestCase):
    def test_single_unit_bias_interval_is_the_95_ci(self):
        pairs = [(1 + i / 1000, 1) for i in range(-5, 7)]
        state = statistics.bootstrap(pairs, 3)
        self.assertEqual(scheduler.aa_bias_test(state, 1, 3)["interval"], state["ci95"])

    def test_adjusted_interval_is_from_the_same_bootstrap_and_contains_the_95_ci(self):
        rng = random.Random(1)
        pairs = [(math.exp(rng.gauss(.001, .003)), 1.0) for _ in range(30)]
        state = statistics.bootstrap(pairs, 11)
        distribution = scheduler.bootstrap_distribution(pairs, 11)
        self.assertEqual([distribution[125], distribution[4875]], state["ci95"])
        test = scheduler.aa_bias_test(state, 7, 11)
        self.assertEqual(test["interval"], [distribution[17], distribution[4982]])
        self.assertLessEqual(test["interval"][0], state["ci95"][0])
        self.assertGreaterEqual(test["interval"][1], state["ci95"][1])
        self.assertAlmostEqual(test["per_unit_alpha"], .05 / 7)

    def test_session_stopping_and_aa_passed_use_the_family_wise_decision(self):
        # Four units; "lineage" carries a real -0.38% A/A placement bias.
        rng = random.Random(4)
        bias = {"baseline": 1.0, "candidate": 1.0, "baseline_twin": 1.0}

        def measure(case, build):
            factor = 1.0038 if case == "lineage" and build == "baseline" else bias[build]
            return {"correct": True, "ns": int(1e9 * factor * math.exp(rng.gauss(0, .003)))}
        units = list(TASK_UNITS["sqlglot-mozilla-transpile"])
        result = scheduler.session(units, measure, seed=2, maximum=20, aa_twin="baseline_twin")
        self.assertEqual(result["aa_bias_test"]["family_size"], 4)
        self.assertEqual(result["aa_bias_test"]["biased_units"], ["lineage"])
        self.assertFalse(result["aa_passed"])
        for case, state in result["cases"].items():
            test = state["aa"]["bias_test"]
            self.assertEqual(test["family_size"], 4)
            self.assertEqual(state["session_stop"]["aa_centered"], not test["biased"], case)
        result["quality"] = {"noisy": False}
        verdict = score_result.interpret(result)
        # The scheduler still records the statistical bias, but -0.38% lies inside the
        # ±2.5% equivalence band, so the verdict does not treat the session as biased.
        self.assertNotIn("aa_unresolved_or_biased", verdict["reason_codes"])
        self.assertEqual(verdict["aa_bias_test"]["biased_units"], [])


class FamilyWiseRateTests(unittest.TestCase):
    def test_k7_family_wise_false_alarms_are_controlled(self):
        # Seven fair units at the 30-pair cap. A per-unit 95% check flags at
        # least one unit in about 1 - 0.95^7 = 30% of fair sessions (more, since
        # the percentile bootstrap is slightly narrow at n=30). Bonferroni brings
        # that close to 5%; the remaining excess is the same small-sample
        # bootstrap narrowness (about 8% expected at n=30, toward 5% as n grows).
        rng = random.Random(2026)
        k, sessions, n, draws = 7, 60, 30, 1500
        adjusted = unadjusted = 0
        for session in range(sessions):
            any_adjusted = any_unadjusted = False
            for unit in range(k):
                seed = session * k + unit
                pairs = [(math.exp(rng.gauss(0, .003)), 1.0) for _ in range(n)]
                any_adjusted |= scheduler.aa_bias_test({"pairs": pairs}, k, seed, draws)["biased"]
                # statistics.bootstrap's 95% CI, read from the same draws.
                distribution = scheduler.bootstrap_distribution(pairs, seed, draws)
                low, high = distribution[int(draws * .025)], distribution[int(draws * .975)]
                any_unadjusted |= not low <= 1 <= high
            adjusted += any_adjusted
            unadjusted += any_unadjusted
        self.assertLessEqual(adjusted / sessions, .10)
        self.assertGreaterEqual(unadjusted / sessions, .25)


if __name__ == "__main__":
    unittest.main()
