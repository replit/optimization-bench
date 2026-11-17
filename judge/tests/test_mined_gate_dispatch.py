import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge.gates import execute_suites


class MinedGateDispatch(unittest.TestCase):
    def test_configured_gate_accepts_expanded_gate_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "mined-task"
            (task / "tests").mkdir(parents=True)
            (task / "tests/judge.json").write_text(json.dumps({"upstream_gate": {}}))
            expected = {"upstream": {"passed": True, "outcomes": {"test": "passed"}}}
            with patch("judge.configured_gates.execute", return_value=expected) as configured:
                observed = execute_suites(task, task / "source", task / "build", "image", "0-7",
                                          output_dir=task / "logs", cache_dir=task / "cache",
                                          fail_fast=True)
            self.assertEqual(observed, expected)
            configured.assert_called_once_with(task, task / "source", task / "build", "image", "0-7",
                                              task / "logs")
