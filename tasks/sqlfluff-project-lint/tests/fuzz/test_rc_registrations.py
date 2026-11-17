"""Validate public request registration without executing candidate code."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

PACKAGE = "_sqlfluff_rc_registration_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules[PACKAGE] = package
cli = importlib.import_module(PACKAGE + ".rc_cli")


class RegistrationTests(unittest.TestCase):
    def test_fixed_public_cli_cases_are_admitted(self):
        cases = cli.public_cases()
        self.assertEqual(len(cases), 3)
        for case in cases:
            self.assertTrue(cli.precondition(case, cli.surface_for(case))["valid"])

    def test_tampering_and_private_scope_are_rejected(self):
        case = cli.public_cases()[0]
        case["input"]["sql"] = "private_or_invalid"
        self.assertFalse(cli.precondition(case, cli.surface_for(case))["valid"])
        case = cli.public_cases()[0]
        surface = cli.surface_for(case)
        surface["visibility"] = "private"
        self.assertFalse(cli.precondition(case, surface)["valid"])

    def test_requests_watch_files_and_request_all_stdout_faults(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = cli.requests({"out": directory})
            self.assertTrue(requests["applicable"])
            self.assertEqual([s["payload_class"] for s in requests["cases"]],
                             ["small", "large", "small"])
            for spec in requests["cases"]:
                expected = cli.FAULTS if spec["case"]["operation"] == "cli_fix" else cli.FAULTS[:-1]
                self.assertEqual(set(spec["faults"]), set(expected))
                self.assertTrue(spec["watch_inputs"])
                self.assertTrue(Path(spec["inputs"], "project/case.sql").is_file())
            self.assertEqual(requests["cases"][-1]["required_outputs"], ["project/case.fixed.sql"])

    def test_header_scope_binds_actual_public_inventory(self):
        request = cli.header_request({"task": str(Path(__file__).resolve().parents[2])})
        self.assertFalse(request["applicable"])
        proof = request["applicability_evidence"]
        self.assertEqual(proof["header_inventory"]["count"], 0)
        self.assertEqual(len(proof["scope_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
