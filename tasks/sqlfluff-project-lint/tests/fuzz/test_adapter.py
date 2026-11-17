"""Task-owned unit checks; not a fuzz/coverage/mutation qualification receipt."""
import copy
import random
import unittest

import adapter


class AdapterTests(unittest.TestCase):
    def cases(self):
        return list(adapter.generate(random.Random(1729), {"cases": 120}))

    def test_fixed_seed_replays_exactly(self):
        self.assertEqual(self.cases(), self.cases())

    def test_every_generated_case_satisfies_precondition(self):
        for case in self.cases():
            self.assertTrue(adapter.precondition(case, None)["valid"], case)
        self.assertEqual({c["operation"] for c in self.cases()}, adapter.OPERATIONS)

    def test_tamper_and_unknown_private_surface_rejected(self):
        case = self.cases()[0]
        case["input"]["sql"] += " changed"
        self.assertFalse(adapter.precondition(case, None)["valid"])
        case = self.cases()[0]
        case["surface_id"] = "sqlfluff.private:invalid"
        case = adapter.seal(case)
        self.assertFalse(adapter.precondition(case, None)["valid"])

    def test_shrinks_keep_surface_domain_faults_and_sequence(self):
        for case in self.cases():
            for small in adapter.shrink(case):
                self.assertTrue(adapter.precondition(small, None)["valid"])
                for key in ("surface_id", "contract_id", "faults", "sequence"):
                    self.assertEqual(small[key], case[key])
                self.assertEqual(small["input"]["domain"], case["input"]["domain"])

    def observation(self, case):
        return {"format": "optimbench-fuzz-observation-v1",
                "identity": {"task": adapter.TASK, "surface_id": case["surface_id"],
                             "case_sha256": case["case_sha256"], "build_sha256": "test-only"},
                "termination": {"kind": "return", "exit_code": 0},
                "outcome": {"kind": "ok", "value": None},
                "effects": {}, "diagnostics": {}}

    def test_diagnostics_do_not_change_contract_oracle(self):
        case = self.cases()[0]
        left = self.observation(case)
        right = copy.deepcopy(left)
        right["diagnostics"] = {"peak_memory_bytes": 999, "scheduler_order": [2, 1]}
        right["identity"]["build_sha256"] = "different-arm"
        self.assertTrue(adapter.compare(left, right, case)["equal"])

    def test_exact_error_text_and_stream_effects_not_erased(self):
        case = self.cases()[0]
        left = self.observation(case)
        right = copy.deepcopy(left)
        left["outcome"] = {"kind": "error", "class": "ValueError", "message": "first"}
        right["outcome"] = {"kind": "error", "class": "ValueError", "message": "second"}
        self.assertFalse(adapter.compare(left, right, case)["equal"])

    def test_malformed_transport_is_not_an_empty_pass(self):
        case = self.cases()[0]
        with self.assertRaises(ValueError):
            adapter.compare({}, {}, case)

    def test_unknown_termination_cannot_compare_equal(self):
        case = self.cases()[0]
        left = self.observation(case)
        left["termination"]["kind"] = "not-a-real-termination"
        with self.assertRaises(ValueError):
            adapter.compare(left, left, case)

    def test_unsupported_state_sequence_rejected(self):
        case = self.cases()[0]
        case["sequence"] = ["init", "private-invalid-state"]
        self.assertFalse(adapter.precondition(adapter.seal(case), None)["valid"])

    def test_every_operation_explores_all_declared_process_counts(self):
        for operation in adapter.OPERATIONS:
            self.assertEqual({c["options"]["processes"] for c in self.cases()
                              if c["operation"] == operation}, {1, 2, 8})


if __name__ == "__main__":
    unittest.main()
