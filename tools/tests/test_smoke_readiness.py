"""Cheap smoke gates reject known integration gaps, never award qualification."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from judge import fuzzing_generic
from judge.fuzzing_io import header_gate
from tools import release_preflight

ROOT = Path(__file__).resolve().parents[2]


class SmokeReadinessTests(unittest.TestCase):
    def task(self, root, name):
        path = root / "tasks" / name
        path.mkdir(parents=True)
        (path / "tests").mkdir()
        settings = {"minimum_pairs": 5, "maximum_pairs": 30, "warmups": 1,
                    "process_state": {"enabled": True, "kind": "native"}}
        (path / "tests/judge.json").write_text(json.dumps(settings))
        return path

    def test_missing_registration_reports_every_blocker_without_running_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(Path(tmp), "missing")
            with patch.object(fuzzing_generic, "task_runners", return_value=({}, None)):
                with self.assertRaisesRegex(ValueError, "stream_faults.*repeat_in_process.*aliasing.*CLI_CASES.*HEADER_CONSUMERS"):
                    release_preflight.generic_registrations(task)

    def test_registration_is_not_native_qualification_or_applicability(self):
        def must_not_execute(*args):
            raise AssertionError("Preflight executed a gate")
        hooks = types.SimpleNamespace(CLI_CASES=must_not_execute, HEADER_CONSUMERS=must_not_execute)
        library = {name: must_not_execute for name in ("stream_faults", "repeat_in_process", "aliasing")}
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(Path(tmp), "registered")
            with patch.object(fuzzing_generic, "task_runners", return_value=(library, hooks)):
                result = release_preflight.generic_registrations(task)
            self.assertEqual(result["status"], "registered")
            self.assertEqual(result["native_execution"], "not_run")
            self.assertNotIn("qualified", result)

    def test_process_state_is_required_independently_of_repeat_callback(self):
        hooks = types.SimpleNamespace(CLI_CASES=lambda c: None, HEADER_CONSUMERS=lambda c: None)
        library = {name: lambda c: None for name in ("stream_faults", "repeat_in_process", "aliasing")}
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(Path(tmp), "registered")
            (task / "tests/judge.json").write_text('{"warmups": 1}')
            with patch.object(fuzzing_generic, "task_runners", return_value=(library, hooks)):
                with self.assertRaisesRegex(ValueError, "process_state"):
                    release_preflight.generic_registrations(task)

    def test_sqlglot_header_scope_has_authenticated_python_only_evidence(self):
        task = ROOT / "tasks/sqlglot-mozilla-transpile"
        _, hooks = fuzzing_generic.task_runners(task)
        raw = hooks.HEADER_CONSUMERS({})
        self.assertFalse(raw["applicable"])
        evidence = raw["applicability_evidence"]
        lock = json.loads((task / "environment/source.lock.json").read_text())
        self.assertEqual(evidence["source_archive_sha256"], lock["upstream"]["sha256"])
        self.assertEqual(evidence["upstream_commit"], lock["upstream"]["commit"])
        self.assertEqual(evidence["scan"]["header_paths"], [])
        self.assertEqual(header_gate({"hooks": hooks})["status"], "not_applicable")

    def test_four_tasks_keep_declared_valid_sampling_not_forced_uniform(self):
        from judge.sampling_policy import resolve
        for task in ("numpy-quantile", "sqlite-distinct-plant-facets",
                     "sqlglot-mozilla-transpile", "git-history-change-summary"):
            with self.subTest(task=task):
                settings = json.loads((ROOT / "tasks" / task / "tests/judge.json").read_text())
                policy = resolve(settings)
                self.assertEqual(policy["effective"]["warmups"], settings["warmups"])
                self.assertEqual(policy["effective"]["minimum_pairs"], settings["minimum_pairs"])
                self.assertEqual(policy["effective"]["maximum_pairs"], settings["maximum_pairs"])
