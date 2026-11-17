"""Regression for conditional protected-driver applicability, not gate verdicts."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        name = "_goldmark_registration_test"
        package = types.ModuleType(name)
        package.__path__ = [str(Path(__file__).parent)]
        sys.modules[name] = package
        spec = importlib.util.spec_from_file_location(
            name+".rc_hooks", Path(__file__).with_name("rc_hooks.py"))
        self.hooks = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.hooks
        spec.loader.exec_module(self.hooks)
        self.addCleanup(lambda: [sys.modules.pop(key, None) for key in list(sys.modules)
                                 if key == name or key.startswith(name+".")])

    def test_driver_scope_binds_the_current_protected_source(self):
        task = Path(__file__).resolve().parents[2]
        scope = json.loads((Path(__file__).parent / "cli-scope.json").read_text())
        driver = scope["benchmark_driver"]
        digest = hashlib.sha256((task / driver["source_path"]).read_bytes()).hexdigest()
        self.assertEqual(driver["sha256"], digest)

    def test_exclusion_binds_both_covering_callbacks_without_claiming_pass(self):
        request = self.hooks.CLI_CASES({})
        driver = request["applicability_evidence"]["benchmark_driver"]
        self.assertEqual(driver["registered_covering_callbacks"],
                         ["stream_faults", "repeat_in_process"])
        self.assertTrue(driver["registration_condition"]["gate_success_still_required"])
        self.assertNotIn("passed", request)
        self.assertEqual(driver["healthy_output_oracle"],
                         "Frozen workload exact/declared comparator; unchanged")

    def test_missing_either_callback_cannot_supply_na_evidence(self):
        for name in ("stream_faults", "repeat_in_process"):
            original = self.hooks.GATES.pop(name)
            try:
                request = self.hooks.CLI_CASES({})
                self.assertEqual(request["applicability_evidence"], {})
                self.assertEqual(request["missing_covering_callbacks"], [name])
                self.assertIn("Unmeasured", request["reason"])
            finally:
                self.hooks.GATES[name] = original
