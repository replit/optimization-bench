import unittest
from judge import scheduler
from judge.verifier import case_roles


class CaseRoleTests(unittest.TestCase):
    def test_correctness_case_has_no_timing_weight_or_schedule_effect(self):
        def measure(case, build):
            return {"correct": True, "ns": None if case == "guard" else 1_000_000_000}
        plain = scheduler.session(["work"], measure, maximum=5)
        with_guard = scheduler.session(["guard", "work"], measure, maximum=5,
                                       roles={"guard": "correctness"})
        self.assertEqual(plain["cases"], with_guard["cases"])
        self.assertEqual(plain["speedup"], with_guard["speedup"])
        self.assertEqual(plain["records"], [r for r in with_guard["records"] if r["case"] == "work"])
        self.assertEqual(len([r for r in with_guard["records"] if r["case"] == "guard"]), 2)

    def test_correctness_mismatch_still_rejects(self):
        def measure(case, build):
            return {"correct": case != "guard" or build == "baseline", "ns": 1}
        with self.assertRaises(scheduler.IncorrectCandidate):
            scheduler.session(["work", "guard"], measure, roles={"guard": "correctness"})

    def test_default_roles_and_split_validation(self):
        self.assertEqual(case_roles({}, ["a"], True), {"a": "performance"})
        self.assertEqual(case_roles({"case_roles": {"public": {"a": "correctness"},
                                                  "heldout": {"b": "performance"}}}, ["a"], True),
                         {"a": "correctness"})
        for settings in ({"case_roles": {"public": {"x": "correctness"}}},
                         {"case_roles": {"public": {"a": "guard"}}}):
            with self.assertRaises(ValueError):
                case_roles(settings, ["a"], True)
