import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from tools import scoring_host as host


class HostRuntimeTests(unittest.TestCase):
    def test_supported_minors_are_explicit_and_parser_lock_unchanged(self):
        requirement = json.loads((host.ROOT / "judge/host-runtime.lock.json").read_text())
        self.assertEqual(requirement["python"]["supported_minor_versions"], ["3.12", "3.14"])
        digest = hashlib.sha256((host.ROOT / "judge/requirements-interface.lock").read_bytes()).hexdigest()
        self.assertEqual(digest, "fc6609afd01423704360ab59a40f7974403874a3da116a280da8c7a17461be6c")

    def test_wrong_interpreter_fails_before_audit_or_parser_checks(self):
        with patch.object(host.sys, "version_info", (3, 11, 9)), patch.object(host.platform, "python_implementation", return_value="CPython"), patch.object(host.sysconfig, "get_config_var", return_value=0), patch.object(host, "parser_check") as parser:
            with self.assertRaises(host.PreflightError) as error:
                host.scoring_preflight()
        parser.assert_not_called()
        self.assertIn("host-interpreter", str(error.exception))
        self.assertIn("3.12/3.14", str(error.exception))
        self.assertNotIn("external-command-audit", str(error.exception))

    def test_free_threaded_abi_is_not_silently_treated_as_locked_wheel_abi(self):
        with patch.object(host.sysconfig, "get_config_var", return_value=1):
            state, failures = host.interpreter_check()
        self.assertTrue(state["gil_disabled"])
        self.assertTrue(failures)

    def test_actual_locked_environment_initializes_all_grammars_and_schema_engine(self):
        state, failures = host.interpreter_check()
        self.assertFalse(failures, failures)
        value, failures = host.parser_check()
        self.assertFalse(failures, failures)
        self.assertNotEqual(sys.prefix, sys.base_prefix, "Run this environment test in the locked judge venv")
        self.assertEqual(len(value["locked_versions"]), 15)
        self.assertEqual(importlib.metadata.version("jsonschema"), "4.19.2")
        self.assertIn(state["minor"], ("3.12", "3.14"))


if __name__ == "__main__":
    unittest.main()
