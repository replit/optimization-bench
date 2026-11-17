import copy
import random
import unittest

import adapter


class AdapterTests(unittest.TestCase):
    def test_generators_deterministic_and_preconditions_valid(self):
        for seed in (1729, 2718, 31415):
            first = list(adapter.generate(random.Random(seed), 120))
            self.assertEqual(first, list(adapter.generate(random.Random(seed), 120)))
            self.assertEqual(len(first), 120)
            self.assertTrue(all(adapter.precondition(case, {})["valid"] for case in first))
            self.assertTrue(any(case["input"]["text"] == "def broken(:\n" for case in first))
            self.assertTrue(any(case["generator_id"] == "explicit-public-corpus-mutation-v1" for case in first))
            self.assertTrue(any(case["faults"] == [{"kind": "readonly-input-file"}] for case in first))
            self.assertTrue(any(case["faults"] == [{"kind": "missing-input-file"}] for case in first))
            self.assertTrue(any({"op": "clear_targets"} in case["sequence"] for case in first))

    def test_unknown_or_private_surface_rejected_before_execution(self):
        case = next(adapter.generate(random.Random(1729), 1))
        case["surface_id"] = "black._format_str_once"
        self.assertFalse(adapter.precondition(case, {})["valid"])

    def test_unsupported_fault_or_diff_not_silently_valid(self):
        case = next(adapter.generate(random.Random(1729), 1))
        case["faults"] = [{"kind": "eintr"}]
        self.assertFalse(adapter.precondition(case, {})["valid"])
        case["faults"] = []
        case["options"]["write_back"] = "DIFF"
        self.assertIn("not_yet_implemented", adapter.precondition(case, {})["reason"])

    def test_shrinking_preserves_surface_options_sequence_faults(self):
        case = next(adapter.generate(random.Random(1729), 1))
        shrinks = list(adapter.shrink(case))
        self.assertTrue(shrinks)
        for shrunk in shrinks:
            self.assertEqual(shrunk["surface_id"], case["surface_id"])
            self.assertEqual(shrunk["options"], case["options"])
            self.assertEqual(shrunk["sequence"], case["sequence"])
            self.assertEqual(shrunk["faults"], case["faults"])
            self.assertTrue(adapter.precondition(shrunk, {})["valid"])

    def test_transport_identity_and_effects_are_required(self):
        case = next(adapter.generate(random.Random(1729), 1))
        observation = {
            "format": "optimbench-fuzz-observation-v1",
            "identity": {"task": adapter.TASK, "build_sha256": "a" * 64,
                         "case_sha256": adapter.case_sha256(case), "surface_id": case["surface_id"]},
            "termination": {"kind": "return", "exit_code": 0},
            "outcome": {"kind": "ok", "value": "x = 1\n"}, "effects": {}, "diagnostics": {},
        }
        self.assertTrue(adapter.compare(observation, observation, case)["equal"])
        self.assertFalse(adapter.compare({}, {}, case)["equal"])
        malformed = {"format": "optimbench-fuzz-observation-v1"}
        self.assertFalse(adapter.compare(malformed, malformed, case)["equal"])
        changed = copy.deepcopy(observation)
        changed["effects"]["file"] = "different"
        self.assertFalse(adapter.compare(observation, changed, case)["equal"])
        changed = copy.deepcopy(observation)
        changed["identity"]["case_sha256"] = "incorrect"
        self.assertFalse(adapter.compare(observation, changed, case)["equal"])

    def test_implementation_diagnostics_do_not_override_semantic_error_message(self):
        case = next(adapter.generate(random.Random(1729), 1))
        base = {"format": "optimbench-fuzz-observation-v1",
                "identity": {"task": adapter.TASK, "build_sha256": "a" * 64,
                             "case_sha256": adapter.case_sha256(case), "surface_id": case["surface_id"]},
                "termination": {"kind": "return", "exit_code": 0},
                "outcome": {"kind": "error", "class": "InvalidInput", "message": "Cannot parse: 1:0"},
                "effects": {}, "diagnostics": {"message": "detail one"}}
        changed = copy.deepcopy(base)
        changed["diagnostics"]["message"] = "detail two"
        self.assertTrue(adapter.compare(base, changed, case)["equal"])
        changed["outcome"]["class"] = "OtherError"
        self.assertFalse(adapter.compare(base, changed, case)["equal"])
        changed = copy.deepcopy(base)
        changed["outcome"]["message"] = "Cannot parse: 2:0"
        self.assertFalse(adapter.compare(base, changed, case)["equal"])

    def test_canonical_unicode_self_hash_and_scalar_types(self):
        case = next(adapter.generate(random.Random(1729), 1))
        case["input"]["text"] = "# λ🙂\nx=1\n"
        raw = adapter.case_sha256(case)
        case["case_sha256"] = raw
        self.assertEqual(raw, adapter.case_sha256(case))
        self.assertTrue(adapter.precondition(case, {})["valid"])
        case["case_sha256"] = "bad"
        self.assertFalse(adapter.precondition(case, {})["valid"])
        self.assertFalse(adapter._typed_equal(True, 1))
        self.assertFalse(adapter._typed_equal(-0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
