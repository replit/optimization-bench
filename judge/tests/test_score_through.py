"""Score-through: failed gates become review flags and timing still runs."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import build, gates, interfaces, policy, scheduler, score, score_result
from judge.verifier import HarnessError

TASK = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"


def wrong_on(bad_case, build_name="candidate", jitter=False):
    calls = []

    def measure(case, build):
        calls.append(case)
        noise = len(calls) % 7 if jitter else 0
        return {"correct": not (build == build_name and case == bad_case),
                "ns": (8 if build == "candidate" else 10) * 10 + noise}
    return measure


class SchedulerScoreThroughTests(unittest.TestCase):
    def test_default_still_stops_on_first_incorrect_output(self):
        with self.assertRaises(scheduler.IncorrectCandidate):
            scheduler.session(["a", "b"], wrong_on("a"), seed=1, maximum=5)

    def test_score_through_times_every_case_and_reports_mismatches(self):
        result = scheduler.session(["a", "b"], wrong_on("a"), seed=1, maximum=5, score_through=True)
        self.assertFalse(result["correct"])
        self.assertTrue(result["score_through"])
        self.assertEqual(result["incorrect_cases"], ["a"])
        self.assertEqual(set(result["cases"]), {"a", "b"})
        self.assertGreater(result["speedup"], 1)

    def test_score_through_correct_candidate_is_unchanged(self):
        result = scheduler.session(["a"], wrong_on(None), seed=1, maximum=5, score_through=True)
        self.assertTrue(result["correct"])
        self.assertEqual(result["incorrect_cases"], [])

    def test_incorrect_unit_is_timed_like_any_other_unit(self):
        result = scheduler.session(["a"], wrong_on("a", jitter=True), seed=1, minimum=5, maximum=30,
                                   half_width=1e-9, score_through=True)
        stop = result["cases"]["a"]["session_stop"]
        self.assertEqual(stop["pairs"], 30)
        self.assertEqual(stop["reason"], "maximum_pairs")

    def test_reference_mismatch_is_never_scored_through(self):
        with self.assertRaises(scheduler.IncorrectCandidate):
            scheduler.session(["a"], wrong_on("a", "baseline"), seed=1, maximum=5, score_through=True)


class InterpretScoreThroughTests(unittest.TestCase):
    def measurement(self, **extra):
        result = scheduler.session(["a"], wrong_on("a"), seed=1, maximum=5, score_through=True)
        result.update(quality={"noisy": False}, **extra)
        return result

    def test_incorrect_scored_through_measurement_keeps_its_timing(self):
        verdict = score_result.interpret(self.measurement())
        self.assertFalse(verdict["correct"])
        self.assertNotEqual(verdict["status"], "incorrect")
        self.assertIsNotNone(verdict["point_speedup"])

    def test_incorrect_without_score_through_is_unscored(self):
        measurement = self.measurement()
        measurement.pop("score_through")
        verdict = score_result.interpret(measurement)
        self.assertEqual(verdict["status"], "incorrect")
        self.assertIsNone(verdict["speedup"])

    def test_summary_lists_flags(self):
        text = score_result.summary({
            "status": "speedup", "speedup": 1.5, "optim_score": 1.4, "task": "t",
            "flags": [{"gate": "runtime_configuration", "status": "needs_review", "correctness": False,
                       "reasons": ["Added process-global runtime configuration requires independent review"]}]})
        self.assertIn("NEEDS REVIEW — scored with flags (not claimable):", text)
        self.assertIn("Flagged for runtime_configuration: needs_review", text)

    def test_summary_says_when_flagged_run_was_not_timed(self):
        text = score_result.summary({
            "status": "invalid run", "task": "t",
            "flags": [{"gate": "interface", "status": "interface_broken", "correctness": True, "reasons": []}]})
        self.assertIn("NEEDS REVIEW — flagged, not timed (not claimable; correctness flagged):", text)


class UpstreamScoreThroughTests(unittest.TestCase):
    def gate(self, root, error, score_through):
        admitted = {"source_sha256": "base", "build_sha256": "base-build", "upstream": {}, "key": "key"}
        with patch("judge.score.reference.verify_for", return_value=admitted), \
             patch("judge.score.policy.envelope", return_value={"files": {}}), \
             patch("judge.score.policy.source_digest", return_value="candidate"), \
             patch("judge.score.build_inventory", return_value="candidate-build"), \
             patch("judge.score.require_baseline"), \
             patch.object(gates, "execute_suites", side_effect=error):
            return score.upstream_gate(TASK, root / "trusted", root / "candidate", "image",
                                       {"cpuset": "0-7"}, root / "out", root / "cache",
                                       rebuilt_reference={}, score_through=score_through)

    def test_configured_suite_failure_is_recorded_not_raised(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, _ = self.gate(root, ValueError("Configured upstream gate failed: 3 tests"), True)
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["rejected"], "Configured upstream gate failed: 3 tests")
            self.assertIsNone(receipt["candidate"])

    def test_rejected_suite_keeps_its_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            error = gates.UpstreamRejected("Upstream command failed: unit", {"unit": {"passed": False}})
            path, _ = self.gate(root, error, True)
            self.assertEqual(json.loads(path.read_text())["candidate"], {"unit": {"passed": False}})

    def test_stop_on_gate_and_harness_faults_still_raise(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Configured upstream gate failed"):
                self.gate(root, ValueError("Configured upstream gate failed"), False)
            with self.assertRaisesRegex(HarnessError, "blocked"):
                self.gate(root, HarnessError("blocked"), True)


class RunTaskScoreThroughTests(unittest.TestCase):
    def run_task(self, root, score_through=True, **overrides):
        args = SimpleNamespace(task=TASK.name, artifacts=root, image="image", out=root / "out",
            cache=root / "cache", cpuset="0-7", agent_host_fingerprint=None,
            separate_sandbox=True, seed=1, minimum=5, maximum=5, half_width=.02, warmups=1,
            gates_only=False, gate_profile="v1.1-rc", score_through=score_through)
        execution = {"cpuset": "0-7", "host": {}, "pinning_took_effect": True,
                     "requested_cores": list(range(8)), "available_cores": list(range(8))}
        upstream_receipt = root / "upstream.json"
        upstream_receipt.write_text(json.dumps(overrides.pop("upstream", {})))
        patches = {
            "heldout.check": {"status": "unavailable"}, "artifacts": ({}, {}),
            "ensure_image": "image", "score_host.select": execution,
            "public_input_preflight.ensure_public_inputs": {"status": "ready"},
            "score_host.require_separate": {}, "assets": root / "trusted",
            "project_assets": root / "projects",
            "reference.ensure": {"build": root / "reference", "key": "key", "build_sha256": "ref"},
            "policy.repair": {"restored": [], "removed": []},
            "build.build": {}, "interfaces.check": {"passed": True},
            "behavior.check": {"process_state": {"passed": True}},
            "policy.envelope": {"files": {}, "runtime_configuration_review": []},
            "runtime_state.review": {"runtime_configuration_review": []},
            "upstream_gate": (upstream_receipt, "sha"),
            "fuzzing_generic.check": {"passed": True, "status": "gates_passed"},
        }
        measurement = scheduler.session(["a"], wrong_on(overrides.pop("wrong_case", None)),
                                        seed=1, maximum=5, score_through=True)
        measurement.update(quality={"noisy": False}, host={}, public_only=True, protocol="p", seed=1,
                           candidate_build_sha256="build")
        cpu = {"timing_performed": False, "records": [
            {"cpu_count": count, "actual_cpus": list(range(count)), "affinity_verified": True,
             "passed": True, "baseline_cases": 1, "candidate_cases": 1} for count in (1, 2, 4, 8)]}
        side_effects = overrides.pop("side_effects", {})
        patches.update(overrides)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("judge.image_recipe.observation", return_value={}))
            for name, value in patches.items():
                stack.enter_context(patch("judge.score." + name, return_value=value,
                                          side_effect=side_effects.get(name)))
            stack.enter_context(patch("judge.cpu_correctness.check", return_value=cpu))
            timing = stack.enter_context(patch.object(score, "evaluate", return_value=measurement))
            result = score.run_task.__wrapped__(TASK, args, {})
        score.finalize_flags(result)
        return result, timing

    def test_review_only_flag_keeps_a_correct_scored_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, timing = self.run_task(
                Path(temporary), **{"runtime_state.review": {"runtime_configuration_review": [{"line": 1}]}})
        timing.assert_called_once()
        self.assertTrue(timing.call_args.kwargs["score_through"])
        self.assertEqual(result["flagged_gates"], ["runtime_configuration"])
        self.assertTrue(result["correct"])
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["claimable"])
        self.assertEqual(result["status"], "speedup")
        self.assertIsNotNone(result["optim_score"])

    def test_every_failed_gate_is_flagged_and_timing_still_runs(self):
        broken = interfaces.InterfaceBroken({"removed": ["minify"]})
        generic = {"passed": False, "status": "generic_gates_pending",
                   "gates": {"aliasing": {"status": "pending", "reason": "runner missing"},
                             "cli_faults": {"status": "passed"}}}
        with tempfile.TemporaryDirectory() as temporary:
            result, timing = self.run_task(
                Path(temporary), upstream={"rejected": "Configured upstream gate failed"},
                wrong_case="a", side_effects={"interfaces.check": broken},
                **{"fuzzing_generic.check": generic})
        timing.assert_called_once()
        flags = {item["gate"]: item for item in result["flags"]}
        self.assertEqual(set(flags), {"interface", "upstream", "generic", "outputs"})
        self.assertTrue(flags["interface"]["correctness"])
        self.assertTrue(flags["upstream"]["correctness"])
        self.assertFalse(flags["generic"]["correctness"])
        self.assertEqual(flags["generic"]["reasons"], ["aliasing: pending (runner missing)"])
        self.assertEqual(result["upstream_gate"], "rejected (scored through)")
        self.assertFalse(result["correct"])
        self.assertFalse(result["claimable"])

    def test_failed_build_scores_zero_without_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, timing = self.run_task(
                Path(temporary), side_effects={"build.build": build.BuildFailed("Candidate build failed: boom")})
        timing.assert_not_called()
        self.assertEqual(result["status"], "build_failed")
        self.assertEqual(result["optim_score"], 0.0)
        self.assertFalse(result["correct"])
        self.assertEqual(result["flagged_gates"], ["build"])
        self.assertTrue(result["flags"][0]["correctness"])

    def test_stop_on_gate_keeps_a_failed_build_a_harness_error(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(build.BuildFailed):
            self.run_task(Path(temporary), score_through=False,
                          side_effects={"build.build": build.BuildFailed("Candidate build failed")})

    def test_unrepairable_source_scores_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, timing = self.run_task(
                Path(temporary), side_effects={"policy.envelope": ValueError("verifier_macro_tamper")})
        timing.assert_not_called()
        self.assertEqual(result["status"], "source_rejected")
        self.assertEqual(result["optim_score"], 0.0)
        self.assertEqual(result["flags"][0]["reasons"], ["verifier_macro_tamper"])

    def test_ignored_out_of_scope_edits_are_flagged_and_still_timed(self):
        repairs = {"restored": ["src/tests/test_a.py"], "removed": ["src/notes.txt"]}
        with tempfile.TemporaryDirectory() as temporary:
            result, timing = self.run_task(Path(temporary), **{"policy.repair": repairs})
        timing.assert_called_once()
        self.assertEqual(result["flagged_gates"], ["source"])
        self.assertFalse(result["flags"][0]["correctness"])
        self.assertEqual(result["flags"][0]["repairs"], repairs)
        self.assertTrue(result["correct"])
        self.assertFalse(result["claimable"])


class SourceRepairTests(unittest.TestCase):
    RULES = {"source_roots": ["src"], "allowed_extensions": [".py"], "protected_paths": [],
             "max_source_files": 100, "max_source_bytes": 1_000_000}

    def test_out_of_scope_edits_are_undone_and_test_additions_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pristine, export = root / "pristine", root / "export"
            for tree in (pristine / "src", export):
                (tree / "tests").mkdir(parents=True)
                (tree / "a.py").write_text("x = 1\n")
                (tree / "tests/test_a.py").write_text("def test():\n    pass\n")
                (tree / "data.json").write_text("{}\n")
            (export / "a.py").write_text("x = 2\n")
            (export / "tests/test_a.py").write_text("def test():\n    assert False\n")
            (export / "data.json").unlink()
            (export / "notes.txt").write_text("scratch\n")
            (export / "tests/test_b.py").write_text("def test_b():\n    pass\n")
            os.symlink("a.py", export / "alias.py")
            with self.assertRaises(ValueError):
                policy.artifacts_to_envelope({"src": str(export)}, pristine, root / "strict", self.RULES)
            repairs = policy.repair({"src": str(export)}, pristine, root / "repaired", self.RULES)
            self.assertEqual(repairs, {"restored": ["src/data.json", "src/tests/test_a.py"],
                                       "removed": ["src/alias.py", "src/notes.txt"]})
            with patch("judge.runtime_state.findings", return_value=[]):
                admitted = policy.envelope(root / "repaired", pristine, self.RULES)
        self.assertEqual(admitted["files"]["src/a.py"], "x = 2\n")
        self.assertIn("src/tests/test_b.py", admitted["test_changes"])


class MainScoreThroughTests(unittest.TestCase):
    def main(self, root, flags, error=None):
        def run(args, result):
            self.assertTrue(args.score_through)
            result.update(score_through=True, flags=flags)
            if error:
                raise error
            result.update(status="speedup", correct=True, optim_score=1.2, speedup=1.2)
        argv = ["judge.score", "--task", TASK.name, "--artifacts", str(root), "--out", str(root / "out")]
        with patch.object(sys, "argv", argv), patch.object(score, "run", side_effect=run), \
             patch("builtins.print"):
            code = score.main()
        return code, json.loads((root / "out/result.json").read_text())

    def test_review_only_flags_exit_2_and_correctness_flags_exit_1(self):
        review = [{"gate": "runtime_configuration", "status": "needs_review", "reasons": [], "correctness": False}]
        wrong = [{"gate": "outputs", "status": "incorrect", "reasons": [], "correctness": True}]
        with tempfile.TemporaryDirectory() as temporary:
            code, result = self.main(Path(temporary) / "review", review)
            self.assertEqual(code, 2)
            self.assertTrue(result["correct"])
            code, result = self.main(Path(temporary) / "wrong", wrong)
            self.assertEqual(code, 1)
            self.assertFalse(result["correct"])

    def test_flags_are_finalized_when_timing_fails(self):
        flags = [{"gate": "interface", "status": "interface_broken", "reasons": [], "correctness": True}]
        with tempfile.TemporaryDirectory() as temporary:
            code, result = self.main(Path(temporary), flags, HarnessError("host changed"))
        self.assertEqual(result["status"], "invalid run")
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["correct"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
