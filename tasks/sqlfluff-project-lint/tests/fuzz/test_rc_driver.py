"""Public constructor-valid controls; no unsupported frozen-object mutation."""
import unittest

import adapter
import rc_driver


class RegressionCases(unittest.TestCase):
    def test_two_contract_controls(self):
        cases = rc_driver.cases()
        self.assertEqual({c["operation"] for c in cases},
                         {"block_config_state", "frozen_match_result"})
        for case in cases:
            self.assertTrue(adapter.precondition(case, rc_driver.surface_for(case))["valid"])
            self.assertEqual(case["case_sha256"], adapter.case_hash(case))

    def test_replay_case_identity_is_stable(self):
        self.assertEqual(rc_driver.cases(), rc_driver.cases())

    def test_frozen_control_has_only_documented_value_operations(self):
        frozen = next(c for c in rc_driver.cases() if c["operation"] == "frozen_match_result")
        self.assertEqual(frozen["sequence"], ["init", "empty", "append", "empty"])
        self.assertEqual(frozen["faults"], [])
        self.assertIn("identity-only interning", rc_driver.surface_for(frozen)["contract_basis"])


if __name__ == "__main__":
    unittest.main()
