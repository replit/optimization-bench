import copy
from pathlib import Path
import tempfile
import unittest

from judge.fuzzing_contract import public_case, digest
from judge.fuzzing_generic import task_runners

class ExistingCLIRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.task = Path(__file__).resolve().parents[2]
        _, self.hooks = task_runners(self.task)
        self.temporary = tempfile.TemporaryDirectory(prefix="sqlfmt-cli-registration-test-")
        self.addCleanup(self.temporary.cleanup)
        self.request = self.hooks.CLI_CASES({"task": self.task, "out": self.temporary.name})

    def test_existing_fixtures_are_public_and_admitted(self):
        self.assertEqual(self.request["format"], "judge-generic-cli-cases-v1")
        self.assertTrue(self.request["applicable"])
        self.assertEqual({row["payload_class"] for row in self.request["cases"]}, {"small", "large"})
        for spec in self.request["cases"]:
            self.assertTrue(public_case(spec["case"], spec["surface"], self.hooks.CLI_PRECONDITION)["valid"])
            self.assertEqual(spec["step"]["argv"][0], "{build}/sqlfmt")

    def test_rewrite_uses_real_changing_content_and_watches_effects(self):
        spec = self.request["cases"][0]
        self.assertEqual(spec["step"]["argv"][1:], ["-d", "-w", "project/input.sql"])
        self.assertEqual(spec["required_outputs"], ["project/input.sql"])
        self.assertEqual(spec["output_dirs"], ["project"])
        self.assertIn("project", spec["watch_inputs"])
        self.assertEqual((Path(spec["inputs"])/"project/input.sql").read_bytes(),
                         b"select a,b from t where a=1;\n")
        self.assertEqual(set(spec["faults"]), {"healthy", "dev_full", "closed_reader",
                                              "closed_stdout", "readonly_dir"})

    def test_altered_argv_or_content_cannot_be_admitted(self):
        for field in ("argv", "sql_sha256"):
            case = copy.deepcopy(self.request["cases"][0]["case"])
            if field == "argv":
                case["options"]["argv"] = ["-l", "-w", "project/input.sql"]
            else:
                case["input"]["sql_sha256"] = "0" * 64
            case["case_sha256"] = digest({k: v for k, v in case.items() if k != "case_sha256"})
            self.assertFalse(self.hooks.CLI_PRECONDITION(case, {})["valid"])

    def test_case_self_commitment_is_required(self):
        case = copy.deepcopy(self.request["cases"][0]["case"])
        case["case_sha256"] = "0" * 64
        self.assertFalse(self.hooks.CLI_PRECONDITION(case, {})["valid"])

if __name__ == "__main__":
    unittest.main()
