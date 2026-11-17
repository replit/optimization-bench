"""Unscored fixtures for the complete alternate workload contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "tasks/sqlfluff-project-lint/environment/workload"
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import canonical_json

spec = importlib.util.spec_from_file_location("alternate_test_task", WORKLOAD / "task.py")
task = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task)
alternate = task.alternates


class AlternateContractTest(unittest.TestCase):
    def exercise(self, root, operation, output, stderr=b"\x00diagnostic\xff", code=1):
        ctx = SimpleNamespace(scratch=root)
        case = SimpleNamespace(id="fixture", data={"operation": operation})
        raw = output if isinstance(output, bytes) else json.dumps(output).encode()
        (root / "step-0.stdout").write_bytes(raw)
        (root / "step-0.stderr").write_bytes(stderr)
        return alternate.collect(case, ctx, [SimpleNamespace(exit_code=code)], task.autofix.snapshot)["fixture"]

    def setup_input(self, root):
        project = root / "project"
        project.mkdir()
        (project / "input.sql").write_text("select 1\n")
        alternate.capture(root, task.autofix.snapshot)

    def test_complete_snowflake_output_and_broken_result_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_input(root)
            rows = [
                {"filepath": "a.sql", "statistics": {"source_chars": 10},
                 "violations": [{"description": "full diagnostic", "nested": {"timings": 9}}],
                 "timings": {"parsing": 0.2}},
                {"filepath": "b.sql", "statistics": {"source_chars": 20}, "violations": []},
            ]
            baseline = self.exercise(root, "snowflake-lint", rows)
            result = json.loads(baseline)
            self.assertNotIn("timings", result["output"][0])
            self.assertEqual(result["output"][0]["violations"][0]["nested"]["timings"], 9)
            self.assertEqual(result["stderr_base64"], "AGRpYWdub3N0aWP/")
            wrong = copy.deepcopy(rows)
            wrong[0]["violations"][0]["description"] = "wrong result"
            for broken in (wrong, rows[:-1], b""):
                with self.subTest(broken=type(broken).__name__):
                    self.assertNotEqual(baseline, self.exercise(root, "snowflake-lint", broken))
            self.assertNotEqual(baseline, self.exercise(root, "snowflake-lint", rows, code=0))
            slower = copy.deepcopy(rows)
            slower[0]["timings"]["parsing"] = 100
            self.assertEqual(baseline, self.exercise(root, "snowflake-lint", slower))

    def test_every_tree_field_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_input(root)
            output = {
                "format": "sqlfluff-complete-syntax-v1", "sqlfluff_version": "4.4.0",
                "settings": {"templater": "raw"}, "tree_options": {"include_position": True},
                "files": [{"path": "a.sql", "source_text": "select 1",
                           "variants": [{"templated_text": "select 1", "tree": [
                               "file", ["keyword", "select", {"line_no": 1, "line_pos": 1}]],
                               "lexing_violations": [], "parsing_violations": []}],
                           "templating_violations": []}],
            }
            result = json.loads(self.exercise(root, "syntax-tree", output, code=0))
            self.assertEqual(result["output"], output)
            self.assertTrue(result["inputs_unchanged"])

    def test_input_changes_and_metadata_changes_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_input(root)
            baseline = self.exercise(root, "syntax-tree", {})
            (root / "project/input.sql").write_text("different")
            changed = json.loads(self.exercise(root, "syntax-tree", {}))
            self.assertFalse(changed["inputs_unchanged"])
            self.assertNotEqual(baseline, canonical_json(changed))
            alternate.capture(root, task.autofix.snapshot)
            path = root / "project/input.sql"
            path.chmod((path.stat().st_mode & 0o777) ^ 0o100)
            changed_mode = json.loads(self.exercise(root, "syntax-tree", {}))
            self.assertFalse(changed_mode["inputs_unchanged"])

    def test_physical_metadata_is_not_a_cross_host_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_input(root)
            ordinary = self.exercise(root, "syntax-tree", {})
            before = json.loads((root / alternate.BEFORE).read_text())
            before["."]["nlink"] += 10
            before["."]["inode"] += 100
            (root / alternate.BEFORE).write_text(json.dumps(before))
            with patch.object(task.autofix, "snapshot", return_value=before):
                other_host = self.exercise(root, "syntax-tree", {})
            self.assertEqual(ordinary, other_host)

    def test_paths_and_operation_count_are_checked(self):
        for name in ("../input.sql", "/input.sql", "./input.sql", "-input.sql"):
            with self.assertRaises(ValueError):
                alternate.relative(name)
        with self.assertRaisesRegex(ValueError, "exactly once"):
            alternate.collect(None, None, [], None)


if __name__ == "__main__":
    unittest.main()
