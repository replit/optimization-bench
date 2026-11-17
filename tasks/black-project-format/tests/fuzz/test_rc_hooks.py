"""Request registration tests; no native execution or verdict substitution."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
NAME = "_black_registration_unit"
package = types.ModuleType(NAME)
package.__path__ = [str(HERE)]
sys.modules[NAME] = package
spec = importlib.util.spec_from_file_location(NAME + ".rc_hooks", HERE / "rc_hooks.py")
hooks = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hooks
spec.loader.exec_module(hooks)


class RegistrationTests(unittest.TestCase):
    def test_cli_requests_have_public_cases_and_real_io(self):
        with tempfile.TemporaryDirectory() as directory:
            result = hooks.CLI_CASES({"out": Path(directory)})
            self.assertEqual(result["format"], "judge-generic-cli-cases-v1")
            self.assertTrue(result["applicable"])
            self.assertEqual(len(result["cases"]), 4)
            identities = set()
            for row in result["cases"]:
                case = row["case"]
                self.assertTrue(hooks.CLI_PRECONDITION(case, row["surface"])["valid"])
                identities.add(hooks.adapter.case_sha256(case))
                self.assertTrue({"healthy", "dev_full", "closed_reader", "closed_stdout"}
                                <= set(row["faults"]))
                filename = "input.py" if case["options"]["cli_mode"] == "stdin" else "project/example.py"
                source = row["inputs"] / filename
                self.assertEqual(source.read_text(), case["input"]["text"])
                self.assertEqual(source.stat().st_mode & 0o777, 0o644)
                self.assertEqual(row["inputs"].stat().st_mode & 0o777, 0o755)
                hooks.Step(**row["step"])
                if row["output_dirs"]:
                    self.assertIn("readonly_dir", row["faults"])
                    self.assertEqual(row["required_outputs"], ["project/example.py"])
            self.assertEqual(len(identities), 4)

    def test_header_scope_is_bound_to_independent_review(self):
        result = hooks.HEADER_CONSUMERS({})
        self.assertFalse(result["applicable"])
        proof = result["applicability_evidence"]["scope"]
        self.assertEqual(proof, json.loads((HERE / "header-scope.json").read_text()))
        self.assertEqual(proof["files_scanned"], 460)
        self.assertEqual(proof["header_paths"], [])
        self.assertEqual(proof["independent_review"]["reviewer_role"],
                         "Independent automated public-source header-scope audit")
        self.assertEqual(proof["independent_review"]["independently_verified_file_inventory_sha256"],
                         proof["file_inventory_sha256"])
        self.assertEqual(set(hooks.GATES), {"stream_faults", "aliasing", "repeat_in_process"})

    def test_header_scope_rejects_changed_independent_inventory_bindings(self):
        for key, value in (("reviewer_role", "unreviewed"),
                           ("independently_verified_files", 459),
                           ("independently_verified_file_inventory_sha256", "0" * 64)):
            proof = json.loads((HERE / "header-scope.json").read_text())
            proof["independent_review"][key] = value
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "header-scope.json").write_text(json.dumps(proof))
                with patch.object(hooks, "HERE", Path(directory)):
                    with self.assertRaisesRegex(ValueError, "renewed review"):
                        hooks.HEADER_CONSUMERS({})


if __name__ == "__main__":
    unittest.main()
