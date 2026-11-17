"""One configured gate transport supports mined and Peter identity reports."""
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from judge.configured_gates import execute
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/runner"))
from runner import StepResult


class ConfiguredReportTests(unittest.TestCase):
    def run_report(self, kind, report):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "task"
            (task / "tests").mkdir(parents=True)
            source = Path(temporary) / "source"
            source.mkdir()
            settings = {"build_source_env": "SOURCE", "upstream_gate": {
                "command": ["test-command"], "report": "report.json", "format": kind}}
            (task / "tests/judge.json").write_text(json.dumps(settings))

            def run(steps, ctx, env):
                self.assertEqual(env["SOURCE"], "/scratch/source")
                (ctx.scratch / "report.json").write_text(json.dumps(report))
                return [StepResult(0, b"", b"")]

            with patch("docker_executor.DockerExecutor") as executor:
                executor.return_value.run.side_effect = run
                return execute(task, source, source, "image", "0-7")

    def test_mined_outcome_format_unchanged(self):
        result = self.run_report("outcomes", {"passed": True, "outcomes": {"original-id": "passed"}})
        self.assertEqual(result["upstream"]["outcomes"], {"original-id": "passed"})

    def test_pytest_full_inventory_preserved(self):
        report = {"collected": 1, "exit_status": 0,
                  "outcomes": {"test.py::original": {"call": "passed"}}}
        result = self.run_report("pytest", report)
        self.assertEqual(result["upstream"]["outcomes"], report)

    def test_identity_format_rejects_duplicate_or_failed_tests(self):
        for tests in ([{"identity": "a", "status": "failed"}],
                      [{"identity": "a", "status": "passed"}] * 2, []):
            with self.subTest(tests=tests), self.assertRaises(ValueError):
                self.run_report("identity-tests", {"tests": tests})
