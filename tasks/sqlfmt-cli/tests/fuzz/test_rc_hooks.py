import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("sqlfmt_rc_cases", Path(__file__).with_name("rc_cases.py"))
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)

class PublicDomainTests(unittest.TestCase):
    def test_registered_domains_have_matching_commitments(self):
        for group in cases.cases():
            for case in group:
                self.assertTrue(cases.precondition(case, cases.surface(case))["valid"])
                self.assertEqual(case["case_sha256"], cases.digest(case))

    def test_corrupt_commitment_is_not_admitted(self):
        case = cases.make("select 1;")
        case["input"]["sql_utf8"] = "different"
        self.assertFalse(cases.precondition(case, {})["valid"])

    def test_fault_must_be_a_legal_bounded_reader_schedule(self):
        for fault in ({"kind": "EIO", "after": -1}, {"kind": "EIO", "after": 200},
                      {"kind": "private-state", "after": 0}):
            case = cases.make("select 1;", faults=[fault])
            self.assertFalse(cases.precondition(case, {})["valid"])

    def test_aliasing_domain_has_no_invented_reader_faults(self):
        case = cases.make("select 1;", mode="lex_alias", faults=[{"kind": "EIO", "after": 0}])
        self.assertFalse(cases.precondition(case, {})["valid"])

    def test_private_symbol_cannot_enter_public_domain(self):
        case = copy.deepcopy(cases.make("select 1;"))
        case["surface_id"] = "format.privateHelper"
        case["case_sha256"] = cases.digest(case)
        self.assertFalse(cases.precondition(case, {})["valid"])

if __name__ == "__main__":
    unittest.main()
