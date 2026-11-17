"""Reference-authenticated expected exits with real offline CLI child processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from judge import process_state
from runner import Case, HarnessError, StepResult, canonical_json, fingerprint

FIXTURES = Path(__file__).parent / "fixtures/process_state"


class NativeProcessStateTests(unittest.TestCase):
    def test_raw_worker_identity_receipts_are_diagnostics_not_cross_arm_pid_state(self):
        left = process_state.ObservedState({"threads": 1, "rule_d_leftovers": 0}, {}, "left")
        right = process_state.ObservedState({"threads": 1, "rule_d_leftovers": 0}, {}, "right")
        left.rule_d_observations = [{"step_index": 0, "main_pid": 100, "leftovers": []}]
        right.rule_d_observations = [{"step_index": 0, "main_pid": 200, "leftovers": []}]
        left.native_exit_identities = [{"pid": 100}]
        right.native_exit_identities = [{"pid": 200}]
        with patch.object(process_state, "probe", side_effect=[left, right]):
            result = process_state.check(self.task, self.build, self.build, "image", "0")
        self.assertTrue(result["passed"])
        self.assertEqual(result["native_exit_identities"]["candidate"], [{"pid": 200}])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = self.root / "native-task"
        self.workload = self.task / "environment/workload"
        self.inputs = self.workload / "inputs"
        self.inputs.mkdir(parents=True)
        tests = self.task / "tests"
        tests.mkdir()
        (tests / "judge.json").write_text(json.dumps({
            "process_state": {"enabled": True, "kind": "native"}}))
        shutil.copy(FIXTURES / "native_task.py", self.workload / "task.py")
        self.build = self.root / "build"
        self.build.mkdir()
        shutil.copy(FIXTURES / "parser.py", self.build / "parser.py")
        (self.inputs / "manifest.json").write_text(json.dumps({
            "format": "optimbench-locked-cases-v1", "cases": "cases.json",
            "lock": "inputs.lock.json", "references": "outputs.json"}))
        (self.workload / "cases.json").write_text(json.dumps({"cases": [{"id": "invalid-xml"}]}))
        (self.workload / "inputs.lock.json").write_text('{"files":{}}')
        self.outcome(4)
        self.reference(4)
        self.compiler_code = 0
        self.protocol = "process"
        self.drop_result = False
        self.seen = []

    def outcome(self, code, stdout="complete stdout\n", stderr="XML parse error\n"):
        (self.build / "outcome.json").write_text(json.dumps({
            "code": code, "stdout": stdout, "stderr": stderr}))

    def reference(self, code, stdout=b"complete stdout\n", stderr=b"XML parse error\n"):
        result = StepResult(code, stdout, stderr)
        value = canonical_json({"format": "cli-output-v1", "runs": [result.streams()] * 2})
        size, digest = fingerprint(value)
        (self.workload / "outputs.json").write_text(json.dumps([
            {"id": "invalid-xml", "bytes": size, "sha256": digest}]))

    def execute(self, steps, ctx, environment):
        self.assertEqual(environment["TRUSTED_ADAPTER_ENV"], "set")
        self.assertEqual(steps[0].argv[:3], ["cc", "-shared", "-fPIC"])
        results = [StepResult(self.compiler_code, b"", b"")]
        for step in steps[1:]:
            self.assertEqual(step.env["LD_PRELOAD"], "/scratch/state.so")
            # The unchanged C observer/compiler are mocked; execute the real CLI only.
            child = subprocess.run(step.argv, capture_output=True, timeout=10,
                                   cwd=ctx.scratch, env={**os.environ, **environment})
            results.append(StepResult(child.returncode, child.stdout, child.stderr,
                                      telemetry={"execution_protocol": self.protocol}))
        (ctx.scratch / "native-state.jsonl").write_text(json.dumps({
            "threads": 1, "environment": ["LC_ALL=C"], "children": 0}) + "\n")
        self.seen.append(results)
        return results[:-1] if self.drop_result else results

    def probe(self, public_inputs=None):
        with patch.object(process_state, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = self.execute
            return process_state.probe(self.task, self.build, "offline-fixture", "0",
                                       public_inputs=public_inputs)

    def test_expected_exit_four_matches_protected_reference_and_passes(self):
        with patch.object(process_state, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = self.execute
            result = process_state.check(self.task, self.build, self.build, "offline-fixture", "0")
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual([[step.exit_code for step in results] for results in self.seen],
                         [[0, 4, 4], [0, 4, 4]])
        self.assertEqual(result["baseline"]["native_processes"][0]["threads"], 1)

    def test_zero_is_not_a_substitute_for_expected_exit_four(self):
        self.outcome(0)
        with self.assertRaisesRegex(ValueError, "differs from protected reference"):
            self.probe()

    def test_exit_four_with_changed_stdout_or_stderr_fails(self):
        for stdout, stderr in [("changed stdout", "XML parse error\n"),
                               ("complete stdout\n", "different error")]:
            with self.subTest(stdout=stdout, stderr=stderr):
                self.outcome(4, stdout, stderr)
                with self.assertRaisesRegex(ValueError, "differs from protected reference"):
                    self.probe()

    def test_unexpected_nonzero_fails(self):
        self.outcome(5)
        with self.assertRaisesRegex(ValueError, "differs from protected reference"):
            self.probe()

    def test_crash_or_signal_is_rejected_even_if_a_reference_matches(self):
        for code in [-11, -15]:
            with self.subTest(code=code):
                self.outcome(code)
                self.reference(code)
                with self.assertRaisesRegex(ValueError, "crashed or did not complete"):
                    self.probe()

    def test_existing_zero_exit_contract_and_snapshot_shape_are_unchanged(self):
        self.outcome(0)
        self.reference(0)
        self.assertEqual(self.probe(), {
            "native_processes": [{"threads": 1, "environment": ["LC_ALL=C"], "children": 0}],
            "rule_d_leftovers": 0, "rule_d_leftover_threads": 0})
        self.outcome(4)
        with self.assertRaisesRegex(ValueError, "differs from protected reference"):
            self.probe()

    def test_observer_compile_failure_and_missing_step_are_rejected(self):
        self.compiler_code = 1
        with self.assertRaisesRegex(ValueError, "observer setup or workload result count"):
            self.probe()
        self.compiler_code = 0
        self.drop_result = True
        with self.assertRaisesRegex(ValueError, "observer setup or workload result count"):
            self.probe()

    def test_incomplete_resident_protocol_is_rejected_even_with_matching_output(self):
        self.protocol = "missing-result-stop"
        with self.assertRaisesRegex(ValueError, "crashed or did not complete"):
            self.probe()

    def test_staged_reference_cannot_be_replaced(self):
        staged = self.root / "staged/inputs"
        shutil.copytree(self.workload, staged.parent)
        (staged.parent / "outputs.json").write_text("[]")
        with self.assertRaisesRegex(HarnessError, "Staged public reference metadata differs"):
            self.probe(staged)
        self.assertEqual(self.seen, [])

    def test_empty_or_uncommitted_output_cannot_pass(self):
        from judge.verifier import load_task
        module = load_task(self.task)
        case = module.cases(self.inputs)[0]
        expected = process_state.native_reference(module, case, [case], self.inputs)
        with patch.object(module, "collect", return_value={}):
            with self.assertRaisesRegex(ValueError, "differs from protected reference"):
                process_state.validate_native(module, case, None, [1, 2, 3],
                    [StepResult(0, b"", b""), StepResult(4, b"", b""), StepResult(4, b"", b"")],
                    expected)

    def test_grouped_case_requires_every_document_reference(self):
        from types import SimpleNamespace
        module = SimpleNamespace(references=lambda _: [
            {"path": name, "expect_len": 1, "expect_sha": "a" * 64} for name in ["a", "b", "c"]])
        case = Case("batch-a", {"docs": [{"path": "a"}, {"path": "b"}]})
        expected = process_state.native_reference(module, case, [case, Case("batch-b", {})], self.inputs)
        self.assertEqual(set(expected), {"a", "b"})
        case.data["docs"].append({"path": "missing"})
        with self.assertRaisesRegex(ValueError, "missing protected references"):
            process_state.native_reference(module, case, [case, Case("batch-b", {})], self.inputs)
