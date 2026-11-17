"""Untimed RC entry and generic policy, without claiming pending runners passed."""
import contextlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import fuzzing_generic as generic, score
from judge import fuzzing_contract as contract
from judge import heldout


class GenericPolicyTests(unittest.TestCase):
    def complete(self):
        return {name: {"status": "passed", "evidence": {"observed": True}} for name in generic.GATES}

    def test_pending_never_becomes_pass_and_deep_hardening_is_separate(self):
        result = generic.summarize({"process_state": self.complete()["process_state"]})
        self.assertEqual(result["status"], "generic_gates_pending")
        self.assertFalse(result["passed"])
        self.assertFalse(result["deep_hardening"]["admission_blocker_for_rc"])
        self.assertFalse(result["timing_performed"])
        self.assertEqual(generic.summarize(self.complete())["status"], "gates_passed")

    def test_process_state_needs_review_is_a_hard_block(self):
        values = self.complete()
        values["process_state"] = {"status": "needs_review", "approved": True, "reason": "different state"}
        result = generic.summarize(values)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["passed"])

    def test_not_applicable_requires_scope_proof_and_records_are_not_mutated(self):
        values = self.complete()
        values["aliasing"] = {"status": "not_applicable"}
        result = generic.summarize(values)
        self.assertEqual(result["status"], "invalid run")
        self.assertEqual(values["aliasing"]["status"], "not_applicable")
        values["aliasing"]["applicability_evidence"] = {"public_surface": "immutable-only"}
        self.assertTrue(generic.summarize(values)["passed"])

    def test_real_regression_is_retained_alongside_pending_runners(self):
        result = generic.summarize({"cli_faults": {"status": "contract_regression", "evidence": {"exit": [2, 0]}}})
        self.assertEqual(result["status"], "contract_regression")
        self.assertEqual(result["gates"]["stream_faults"]["status"], "pending")

    def test_check_always_records_process_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = generic.check(root / "task", root / "baseline", root / "candidate", "image", "0-7",
                                   root / "out", process_state={"passed": False},
                                   runners={"cli_faults": lambda ctx: {"status": "passed", "evidence": {"exit": 0}}},
                                   builds={"baseline": "a" * 64, "candidate": "b" * 64})
            self.assertEqual(result["status"], "needs_review")
            self.assertTrue((root / "out/generic-gates.json").is_file())


class TaskHookTests(unittest.TestCase):
    def row(self):
        value = {"task": "example", "surface_id": "public", "contract_id": "contract",
                 "precondition_id": "valid", "input": {}, "options": {}, "sequence": [], "faults": []}
        surface = {"id": "public", "visibility": "public", "contract_id": "contract",
                   "precondition_id": "valid"}
        def observed(arm):
            return {"format": "optimbench-fuzz-observation-v1", "identity": {
                "task": "example", "surface_id": "public", "case_sha256": contract.case_digest(value),
                "build_sha256": ("a" if arm == "baseline" else "b") * 64},
                "termination": {"kind": "return", "exit_code": 0},
                "outcome": {"kind": "ok", "value": True}, "effects": {}, "diagnostics": {}}
        return {"case": value, "surface": surface,
                "baseline": [observed("baseline"), observed("baseline")],
                "candidate": [observed("candidate"), observed("candidate")]}

    def classify(self, name, raw):
        return generic._call_evidence(name, raw, SimpleNamespace(
            PRECONDITION=lambda *_: {"valid": True, "reason": "public domain"}),
            {"builds": {"baseline": "a" * 64, "candidate": "b" * 64}})

    def test_task_supplies_facts_not_a_verdict(self):
        row = self.row()
        raw = {"format": "judge-generic-call-evidence-v1", "applicable": True,
               "status": "passed", "cases": [row]}
        self.assertEqual(self.classify("aliasing", raw)["status"], "passed")
        for observation in row["candidate"]:
            observation["outcome"]["value"] = 1
        self.assertEqual(self.classify("aliasing", raw)["status"], "contract_regression")

    def test_private_case_quarantined_without_observer_credit(self):
        row = self.row()
        row["surface"]["visibility"] = "private"
        row.pop("baseline")
        row.pop("candidate")
        result = self.classify("aliasing", {"format": "judge-generic-call-evidence-v1",
                                           "applicable": True, "cases": [row]})
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["evidence"]["observations"][0]["candidate_verdict"])

    def test_repeat_requires_real_worker_fresh_oracle_and_repeated_lifecycle(self):
        row = self.row()
        row["fresh_baseline"], row["fresh_candidate"] = row["baseline"][:1], row["candidate"][:1]
        raw = {"format": "judge-generic-call-evidence-v1", "applicable": True, "cases": [row],
               "workers": {arm: {"mode": "persistent", "target_library_in_process": True,
                                 "worker_id": arm, "calls": 2} for arm in ("baseline", "candidate")},
               "lifecycle": [{arm: {"available": True,
                    "before": {"fds": 3, "threads": 1, "children": 0},
                    "after": {"fds": 3, "threads": 1, "children": 0}}
                    for arm in ("baseline", "candidate")} for _ in range(2)]}
        for lifecycle in raw["lifecycle"]:
            for arm in ("baseline", "candidate"):
                for point in ("before", "after"):
                    lifecycle[arm][point]["thread_identities"] = [
                        {"tid": 11 if arm == "baseline" else 22, "start_ticks": 100, "state": "R",
                         "tgid": 11 if arm == "baseline" else 22}]
        self.assertEqual(self.classify("repeat_in_process", raw)["status"], "passed")
        for observation in raw["lifecycle"]:
            observation["candidate"]["after"]["fds"] = 4
        self.assertEqual(self.classify("repeat_in_process", raw)["status"], "needs_review")
        raw["lifecycle"][0]["candidate"]["after"]["threads"] = 2
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.classify("repeat_in_process", raw)
        raw["lifecycle"][0]["candidate"]["after"]["threads"] = 1
        raw["workers"]["candidate"]["mode"] = "fresh-process-per-call"
        self.assertEqual(self.classify("repeat_in_process", raw)["status"], "pending")
        raw["workers"]["candidate"]["mode"] = "persistent"
        for observation in raw["lifecycle"]:
            after = observation["candidate"]["after"]
            after["fds"], after["threads"] = 3, 2
            after["thread_identities"].append({"tid": 23, "tgid": 22, "start_ticks": 200, "state": "S"})
        self.assertEqual(self.classify("repeat_in_process", raw)["status"], "pending")
        for observation in raw["lifecycle"]:
            observation["candidate"]["after"]["thread_identities"][-1]["flags"] = 0
        self.assertEqual(self.classify("repeat_in_process", raw)["status"], "needs_review")
        raw["lifecycle"][0]["candidate"]["after"]["thread_identities"][-1]["flags"] = 4
        with self.assertRaisesRegex(ValueError, "Kernel-exiting"):
            self.classify("repeat_in_process", raw)

    def test_thread_identity_replacement_not_count_alone(self):
        before = {"threads": 1, "thread_identities": [{"tid": 12, "tgid": 12, "start_ticks": 100, "state": "S"}]}
        after = {"threads": 1, "thread_identities": [{"tid": 12, "tgid": 12, "start_ticks": 200, "state": "S"}]}
        self.assertNotEqual(generic._thread_snapshot(before), generic._thread_snapshot(after))
        self.assertIsNone(generic._thread_snapshot({"threads": 0, "thread_identities": []}))
        self.assertIsNone(generic._thread_snapshot({"threads": 1, "thread_identities": [
            {"tid": 13, "tgid": 12, "start_ticks": 100, "state": "S"}]}))
        with self.assertRaisesRegex(ValueError, "dead"):
            generic._thread_snapshot({"threads": 1, "thread_identities": [
                {"tid": 12, "start_ticks": 100, "state": "Z"}]})

    def test_relative_task_modules_are_isolated_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = []
            for name in ("first", "second"):
                fuzz = root / name / "tests/fuzz"
                fuzz.mkdir(parents=True)
                (fuzz / "adapter.py").write_text("VALUE = " + repr(name) + "\n")
                (fuzz / "rc_hooks.py").write_text("from .adapter import VALUE\nGATES = {}\n")
                _, module = generic.task_runners(root / name)
                values.append(module.VALUE)
            self.assertEqual(values, ["first", "second"])
            (root / "third/tests").mkdir(parents=True)
            (root / "third/tests/fuzz").symlink_to(root / "first/tests/fuzz")
            with self.assertRaisesRegex(ValueError, "verifier-owned"):
                generic.task_runners(root / "third")


class GatesOnlyScoreTests(unittest.TestCase):
    def test_public_replay_bypasses_outer_private_acquisition_before_lock_read(self):
        with tempfile.TemporaryDirectory() as folder:
            task = Path(folder)
            (task / "tests").mkdir()
            (task / "tests/heldout.lock.json").write_text("not an available private lock")
            @heldout.acquired
            def entry(task, args):
                return "public-only"
            with patch.object(heldout, "materialized_task", side_effect=AssertionError("private data read")):
                self.assertEqual(entry(task, SimpleNamespace(gates_only=True)), "public-only")

    def test_gates_only_never_invokes_measurement_or_interpretation(self):
        task = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            args = SimpleNamespace(task=task.name, artifacts=root, image="image", out=root / "out",
                cache=root / "cache", cpuset="0-7", agent_host_fingerprint=None,
                separate_sandbox=True, seed=1, minimum=5, maximum=5, half_width=.02, warmups=1,
                gates_only=True)
            execution = {"cpuset": "0-7", "host": {}, "pinning_took_effect": True,
                         "requested_cores": list(range(8)), "available_cores": list(range(8))}
            ref = {"build": root / "rebuilt-reference", "key": "key", "build_sha256": "ref"}
            stack.enter_context(patch("judge.image_recipe.observation",
                                     return_value={"runtime_image_id": "image", "recipe_label_matches": False}))
            patches = {
                "heldout.check": {"status": "available"}, "artifacts": ({}, {}),
                "ensure_image": "image", "score_host.select": execution,
                "public_input_preflight.ensure_public_inputs": {"status": "ready"},
                "score_host.require_separate": {}, "assets": root / "trusted",
                "project_assets": root / "projects", "reference.ensure": ref,
                "build.build": {}, "interfaces.check": {"passed": True},
                "behavior.check": {"process_state": {"passed": True}},
                "policy.envelope": {"files": {}, "runtime_configuration_review": []},
                "runtime_state.review": {"runtime_configuration_review": []},
                "upstream_gate": (root / "upstream.json", "sha"),
                "fuzzing_generic.check": {"passed": False, "status": "generic_gates_pending"},
            }
            mocks = {name: stack.enter_context(patch("judge.score." + name, return_value=value))
                     for name, value in patches.items()}
            stack.enter_context(patch("judge.cpu_correctness.check", side_effect=lambda *a, **k:
                mocks["fuzzing_generic.check"].return_value.get("cpu_correctness")))
            measured = stack.enter_context(patch.object(score, "evaluate", side_effect=AssertionError("timing ran")))
            interpreted = stack.enter_context(patch.object(score.score_result, "interpret",
                                                           side_effect=AssertionError("score interpreted")))
            result = score.run_task(task, args, {})
            self.assertEqual(result["status"], "generic_gates_pending")
            self.assertFalse(result["timing_performed"])
            self.assertFalse(result["aa_sampling_performed"])
            self.assertTrue(result["public_only"])
            measured.assert_not_called()
            interpreted.assert_not_called()
            mocks["project_assets"].assert_called_once_with(task, root / "trusted", True)
            self.assertFalse((root / "out/measurements.json").exists())
            mocks["fuzzing_generic.check"].return_value = {"passed": True, "status": "passed"}
            missing_cpu = score.run_task(task, args, {})
            self.assertEqual(missing_cpu["status"], "generic_gates_pending")
            self.assertIsNone(missing_cpu["correct"])
            measured.assert_not_called()
            mocks["fuzzing_generic.check"].return_value["cpu_correctness"] = {
                "timing_performed": False, "records": [
                    {"cpu_count": count, "actual_cpus": list(range(count)),
                     "affinity_verified": True, "passed": True,
                     "baseline_cases": 1, "candidate_cases": 1}
                    for count in (1, 2, 4, 8)]}
            complete = score.run_task(task, args, {})
            self.assertEqual(complete["status"], "passed")
            self.assertTrue(complete["correct"])
            measured.assert_not_called()
            mocks["behavior.check"].return_value = {
                "status": "needs_review", "process_state": {"passed": False}}
            rejected = score.run_task(task, args, {})
            self.assertFalse(rejected["correct"])
            self.assertTrue(rejected["admission_rejected"])
            self.assertEqual(rejected["rejection_policy"], "process-state-hard-block-no-approval")
            measured.assert_not_called()
            interpreted.assert_not_called()
