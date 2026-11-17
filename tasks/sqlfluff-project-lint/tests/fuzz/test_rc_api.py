import copy
import unittest

import rc_api_cases as api


class PublicAPIProfiles(unittest.TestCase):
    def test_profiles_and_commitments_are_valid_and_distinct(self):
        cases = api.repeat_cases() + api.stream_cases()
        self.assertEqual(len({c["case_sha256"] for c in cases}), 9)
        for case in cases:
            self.assertTrue(api.precondition(case, api.surface(case))["valid"])

    def test_private_or_tampered_or_unregistered_fault_is_rejected(self):
        for field, value in (("faults", ["unknown"]), ("surface_id", "private:function")):
            case = copy.deepcopy(api.stream_cases()[0])
            case[field] = value
            self.assertFalse(api.precondition(case)["valid"])

    def test_schedule_contains_A_B_A_and_interleaves_fresh_instances(self):
        self.assertEqual(api.SCHEDULE[:3], (0, 1, 0))
        self.assertTrue(all(api.SCHEDULE.count(i) >= 2 for i in range(4)))
        self.assertEqual(api.repeat_cases()[3]["options"]["instance"], "fresh")

    def test_semantics_compare_does_not_award_diagnostic_credit(self):
        case = api.repeat_cases()[0]
        a = {"termination": {"kind": "return", "exit_code": 0},
             "outcome": {"kind": "ok", "value": "actual"}, "effects": {},
             "diagnostics": {"memory": 1}}
        b = copy.deepcopy(a)
        b["diagnostics"]["memory"] = 9000
        self.assertTrue(api.compare(a, b, case)["equal"])
        b["outcome"]["value"] = "different"
        self.assertFalse(api.compare(a, b, case)["equal"])


if __name__ == "__main__":
    unittest.main()
