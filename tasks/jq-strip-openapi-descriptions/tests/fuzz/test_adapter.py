import copy
import random
import unittest
from adapter import TASK, compare, content_hash, encode, generate, make_case, precondition, shrink


def observation(case, stdout=b"\xff", stderr=b"", status=0):
    return {"format": "optimbench-fuzz-observation-v1",
            "identity": {"task": TASK, "surface_id": case["surface_id"],
                         "case_sha256": content_hash(case), "build_sha256": "0"*64},
            "termination": {"kind": "return", "exit_code": status},
            "outcome": {"kind": "ok" if status == 0 else "error", "value": status},
            "effects": {"stdout": {"encoding": "base64", "data": encode(stdout)},
                        "stderr": {"encoding": "base64", "data": encode(stderr)}}}


class AdapterTests(unittest.TestCase):
    def test_fixed_seed_and_valid_cases(self):
        first = list(generate(random.Random(1729), 64))
        self.assertEqual(first, list(generate(random.Random(1729), 64)))
        self.assertTrue(all(precondition(case, {})["valid"] for case in first))

    def test_invalid_utf8_is_not_replacement_normalized(self):
        case = make_case(b"{}")
        self.assertFalse(compare(observation(case, b"\xff"), observation(case, b"\xfe"), case)["equal"])

    def test_stderr_and_exit_are_contractual(self):
        case = make_case(b"{")
        self.assertFalse(compare(observation(case, stderr=b"a", status=4),
                                 observation(case, stderr=b"b", status=4), case)["equal"])
        self.assertFalse(compare(observation(case, status=4), observation(case, status=0), case)["equal"])

    def test_missing_transport_fails_closed(self):
        case = make_case(b"[]")
        with self.assertRaises(ValueError):
            compare({}, observation(case), case)

    def test_shrink_preserves_surface_and_schedule(self):
        case = make_case(b'{"a":[1,2,3]}')
        for smaller in shrink(case):
            self.assertTrue(precondition(smaller, {})["valid"])
            for field in ("surface_id", "options", "sequence", "faults"):
                self.assertEqual(smaller[field], case[field])

    def test_public_selection_must_be_explicit_and_hash_bound(self):
        with self.assertRaises(ValueError):
            list(generate(random.Random(1), {"cases": 0, "public_cases": [
                {"split": "heldout", "data_b64": encode(b"{}"), "sha256": "0"*64}]}))

    def test_unmapped_fault_or_surface_rejected(self):
        case = make_case(b"[]")
        for field, value in (("faults", [{"kind": "malloc"}]), ("surface_id", "private.helper")):
            other = copy.deepcopy(case)
            other[field] = value
            other["case_sha256"] = content_hash(other)
            self.assertFalse(precondition(other, {})["valid"])


if __name__ == "__main__":
    unittest.main()
