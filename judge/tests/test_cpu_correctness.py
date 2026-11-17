"""CPU policy controls; fake workers never claim physical Docker qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import cpu_correctness as cpu, cpu_policy, fuzzing_contract as contract
from judge import fuzzing_generic as generic

IMAGE = "sha256:" + "c" * 64
BUILDS = {"baseline": "a" * 64, "candidate": "b" * 64}


class CpuCorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.module = SimpleNamespace(PRECONDITION=lambda *_: {"valid": True, "reason": "unit fixture"},
                                      COMPARE=contract.exact_contract_compare)

    def raw(self, ctx):
        case = {"task": "example", "surface_id": "api", "contract_id": "contract",
                "precondition_id": "valid", "input": {}, "options": {},
                "sequence": [], "faults": []}
        surface = {"id": "api", "visibility": "public", "contract_id": "contract",
                   "precondition_id": "valid",
                   "oracle": {"required_error_fields": {"category": "nonempty_string"}}}
        def observation(arm):
            return {"format": "optimbench-fuzz-observation-v1", "identity": {
                "task": "example", "surface_id": "api", "case_sha256": contract.case_digest(case),
                "build_sha256": BUILDS[arm]}, "termination": {"kind": "return", "exit_code": 0},
                "outcome": {"kind": "ok", "value": "same"}, "effects": {},
                "diagnostics": {"execution_affinity": list(ctx["cpu_requested"])}}
        row = {"case": case, "surface": surface,
               **{arm: [observation(arm), observation(arm)] for arm in cpu.ARMS}}
        return {"format": "judge-generic-call-evidence-v1", "applicable": True, "cases": [row]}

    def execute(self, mutate=None, callback=None, **kwargs):
        def run(ctx):
            self.calls.append(ctx["cpuset"])
            ctx["hooks"] = self.module
            raw = self.raw(ctx)
            if mutate:
                mutate(raw, ctx)
            return generic._call_evidence("stream_faults", raw, self.module, ctx)
        runners = {name: lambda _: {"status": "not_applicable",
                   "applicability_evidence": {"test_only_scope": "unit-control"}}
                   for name in cpu.GATES}
        runners["stream_faults"] = callback or run
        return cpu.check(self.root / "example", self.root / "b", self.root / "c",
                         IMAGE, self.root / "out", builds=BUILDS, runners=runners, **kwargs)

    def test_match_all_subsets_and_hash_bound_receipt(self):
        result = self.execute()
        self.assertEqual(self.calls, ["0", "0,1", "0,1,2,3", "0,1,2,3,4,5,6,7"])
        self.assertTrue(cpu_policy.correctness_receipt(result)["passed"])
        self.assertFalse(result["timing_performed"])
        for row in result["records"]:
            self.assertEqual(row["actual_cpus"], list(range(row["cpu_count"])))
            self.assertEqual(row["baseline_cases"], 2)
            self.assertEqual(row["candidate_cases"], 2)
            for binding in (row["execution_evidence"], row["gates"]["stream_faults"]["evidence"]):
                self.assertEqual(hashlib.sha256(Path(binding["path"]).read_bytes()).hexdigest(),
                                 binding["sha256"])
        self.assertEqual(hashlib.sha256(Path(result["evidence"]["path"]).read_bytes()).hexdigest(),
                         result["evidence"]["sha256"])

    def test_wrong_only_at_one_is_regression_with_repro(self):
        def mutate(raw, ctx):
            if len(ctx["cpu_requested"]) == 1:
                for value in raw["cases"][0]["candidate"]:
                    value["outcome"]["value"] = "wrong"
        result = self.execute(mutate)
        self.assertEqual([r["status"] for r in result["records"]],
                         ["contract_regression", "passed", "passed", "passed"])
        self.assertTrue(Path(result["records"][0]["repro"]["path"]).is_file())
        self.assertFalse(cpu_policy.correctness_receipt(result)["passed"])

    def test_one_unstable_mismatch_still_contract_regression(self):
        result = self.execute(lambda raw, _: raw["cases"][0]["candidate"][0]["outcome"].update(value="wrong"))
        self.assertEqual(result["status"], "contract_regression")

    def test_affinity_mismatch_invalid_not_semantic_blame(self):
        result = self.execute(lambda raw, _: raw["cases"][0]["candidate"][0]["diagnostics"].update(
            execution_affinity=[99]))
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(all(not r["affinity_verified"] for r in result["records"]))

    def test_baseline_affinity_also_checked(self):
        result = self.execute(lambda raw, _: raw["cases"][0]["baseline"][0]["diagnostics"].update(
            execution_affinity=[99]))
        self.assertEqual(result["status"], "invalid")

    def test_duplicate_affinity_invalid(self):
        result = self.execute(lambda raw, ctx: raw["cases"][0]["candidate"][0]["diagnostics"].update(
            execution_affinity=ctx["cpu_requested"] * 2))
        self.assertEqual(result["status"], "invalid")

    def test_missing_affinity_unmeasured(self):
        result = self.execute(lambda raw, _: raw["cases"][0]["candidate"][0]["diagnostics"].clear())
        self.assertEqual(result["status"], "unmeasured")

    def test_missing_arm_run_unmeasured(self):
        result = self.execute(lambda raw, _: raw["cases"][0]["candidate"].pop())
        self.assertEqual(result["status"], "unmeasured")

    def test_empty_cases_unmeasured(self):
        result = self.execute(lambda raw, _: raw["cases"].clear())
        self.assertEqual(result["status"], "unmeasured")

    def test_boolean_pass_without_observations_unmeasured(self):
        result = self.execute(callback=lambda _: {"status": "passed", "evidence": {"requested": [0]}})
        self.assertEqual(result["status"], "unmeasured")

    def test_worker_exception_unmeasured_and_continues_subsets(self):
        def broken(_):
            raise RuntimeError("worker unavailable")
        result = self.execute(callback=broken)
        self.assertEqual(result["status"], "unmeasured")
        self.assertEqual(len(result["records"]), 4)

    def test_deadline_not_swallowed_and_alarm_restored(self):
        before = signal.getsignal(signal.SIGALRM)
        def hung(_):
            try:
                time.sleep(4)
            except Exception:
                self.fail("CPU deadline was swallowed by an ordinary callback handler")
        result = self.execute(callback=hung, limits={"seconds_per_subset": 1})
        self.assertEqual(result["status"], "unmeasured")
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)

    def test_count_bound_never_samples(self):
        def extra(raw, _):
            raw["cases"].append(copy.deepcopy(raw["cases"][0]))
        result = self.execute(extra, limits={"cases_per_gate": 1})
        self.assertEqual(result["status"], "unmeasured")

    def test_observation_bound(self):
        result = self.execute(limits={"observations_per_arm": 1})
        self.assertEqual(result["status"], "unmeasured")

    def test_same_error_is_compatible(self):
        def errors(raw, _):
            for arm in cpu.ARMS:
                for value in raw["cases"][0][arm]:
                    value["termination"]["kind"] = "exception"
                    value["outcome"] = {"kind": "error", "category": "ValueError"}
        result = self.execute(errors)
        self.assertTrue(result["passed"])

    def test_task_comparator_is_preserved(self):
        self.module.COMPARE = lambda *_: {"equal": True, "differences": [], "policy_id": "reviewed-test"}
        result = self.execute(lambda raw, _: raw["cases"][0]["candidate"][0]["outcome"].update(value="different"))
        self.assertTrue(result["passed"])

    def test_no_registered_callbacks_never_pass(self):
        result = cpu.check(self.root / "example", self.root / "b", self.root / "c",
                           IMAGE, self.root / "out", builds=BUILDS, runners={})
        self.assertEqual(result["status"], "unmeasured")

    def test_requires_immutable_image(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            cpu.check(self.root / "example", self.root / "b", self.root / "c",
                      "latest", self.root / "out", builds=BUILDS, runners={})

    def cli_proof(self, json_contract=False, changed_value=False):
        from judge.tests import test_fuzzing_io as fixtures
        from judge import fuzzing_io
        fixture = fixtures.SharedIOPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if json_contract:
            from judge.output_contract import DEFAULT
            policy = {**DEFAULT, "stdout": {"kind": "json-structural"}}
            (fixture.task / "tests/judge.json").write_text(json.dumps({"output_comparator": policy}))
        def run(ctx):
            ctx["hooks"] = fixture.hooks
            def pair(**kwargs):
                raw = fixture.pair(**kwargs)
                for pair in raw["observations"].values():
                    for arm, row in pair.items():
                        row.update(execution_affinity=ctx["cpu_requested"], observer_sha256="d" * 64)
                        if json_contract and row["stdout"]["bytes"]:
                            padding = "x" * (65536 if kwargs["payload_class"] == "large" else 8)
                            value = 2 if arm == "candidate" and changed_value else 1
                            payload = ({"value": value, "padding": padding} if arm == "baseline"
                                       else {"padding": padding, "value": value})
                            row["stdout"] = fixtures.stream(json.dumps(payload).encode())
                return raw
            with patch("tools.fuzz_runners.cli_faults.run_pair", side_effect=pair) as worker:
                result = fuzzing_io.cli_gate(ctx)
            self.assertEqual(worker.call_count, 4)  # Existing two payloads, two replays.
            for call in worker.call_args_list:
                self.assertEqual(call.kwargs["cpuset"], ctx["cpuset"])
            return result
        runners = {name: lambda _: {"status": "not_applicable", "applicability_evidence": {"unit": True}}
                   for name in cpu.GATES}
        runners["cli_faults"] = run
        result = cpu.check(fixture.task, self.root / "b", self.root / "c",
                           IMAGE, self.root / "out", builds=BUILDS, runners=runners)
        return result

    def test_cli_uses_existing_shared_callback_and_checks_every_arm(self):
        result = self.cli_proof()
        self.assertTrue(result["passed"], result)
        self.assertTrue(all(row["baseline_cases"] == 20 for row in result["records"]))

    def test_cli_cpu_replay_uses_declared_json_not_raw_hashes(self):
        self.assertTrue(self.cli_proof(json_contract=True)["passed"])

    def test_cli_cpu_replay_still_rejects_changed_json_values(self):
        result = self.cli_proof(json_contract=True, changed_value=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "contract_regression")

    def test_header_normalization_affinity_and_replay_completeness(self):
        context = {"cpu_requested": [0, 1], "cpu_limits": cpu.DEFAULT_LIMITS}
        arm = {"execution_affinity": [0, 1], "cells": [{"id": "cell", "status": "completed"}]}
        raw = {"baseline": copy.deepcopy(arm), "candidate": copy.deepcopy(arm),
               "cells": [{"id": "cell", "attribution": "same_success"}]}
        result = {"evidence": {"runs": [copy.deepcopy(raw), copy.deepcopy(raw)]}}
        runs, repros = cpu._normalize("header_consumers", result, context)
        self.assertEqual(len(runs), 4)
        self.assertFalse(repros)
        result["evidence"]["runs"][1]["candidate"]["execution_affinity"] = [0]
        with self.assertRaises(cpu.InvalidEvidence):
            cpu._normalize("header_consumers", result, context)
        result["evidence"]["runs"].pop()
        with self.assertRaises(cpu.MissingEvidence):
            cpu._normalize("header_consumers", result, context)

    def test_changed_case_inventory_fails_closed(self):
        def mutate(raw, ctx):
            if len(ctx["cpu_requested"]) == 2:
                row = raw["cases"][0]
                row["case"]["input"] = {"changed": True}
                for arm in cpu.ARMS:
                    for value in row[arm]:
                        value["identity"]["case_sha256"] = contract.case_digest(row["case"])
        result = self.execute(mutate)
        self.assertEqual(result["records"][1]["status"], "unmeasured")

    def test_duplicate_run_inventory_fails_closed(self):
        result = self.execute(lambda raw, _: raw["cases"].append(copy.deepcopy(raw["cases"][0])))
        self.assertEqual(result["status"], "unmeasured")

    def test_registration_uses_shared_and_task_callbacks(self):
        def run(ctx):
            self.calls.append(ctx["cpuset"])
            return generic._call_evidence("stream_faults", self.raw(ctx), self.module, ctx)
        shared = {name: lambda _: {"status": "not_applicable", "applicability_evidence": {"unit": True}}
                  for name in cpu.GATES if name != "stream_faults"}
        with patch.object(generic, "RUNNERS", shared), patch.object(
                generic, "task_runners", return_value=({"stream_faults": run}, self.module)) as loader:
            result = cpu.check(self.root / "example", self.root / "b", self.root / "c",
                               IMAGE, self.root / "out", builds=BUILDS)
        loader.assert_called_once()
        self.assertTrue(result["passed"])
        self.assertEqual(len(self.calls), 4)

    def test_pending_does_not_prevent_other_registered_cases_running(self):
        def run(ctx):
            self.calls.append(ctx["cpuset"])
            return generic._call_evidence("stream_faults", self.raw(ctx), self.module, ctx)
        with patch.object(generic, "RUNNERS", {}), patch.object(
                generic, "task_runners", return_value=({"stream_faults": run}, self.module)):
            result = cpu.check(self.root / "example", self.root / "b", self.root / "c",
                               IMAGE, self.root / "out", builds=BUILDS)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(result["status"], "unmeasured")
        self.assertTrue(all(row["baseline_cases"] == 2 for row in result["records"]))

    def test_library_pending_lifecycle_retains_raw_affinity(self):
        ctx = {"cpu_requested": [0], "cpu_limits": cpu.DEFAULT_LIMITS, "builds": BUILDS}
        raw = self.raw(ctx)
        result = generic._task_call("repeat_in_process", lambda _: raw, self.module, ctx)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["evidence"]["cpu_raw"], raw)

    def test_repeat_library_checks_fresh_and_persistent_affinity(self):
        ctx = {"cpu_requested": [0], "cpu_limits": cpu.DEFAULT_LIMITS, "builds": BUILDS,
               "task": self.root / "example", "hooks": self.module}
        raw = self.raw(ctx)
        row = raw["cases"][0]
        for arm in cpu.ARMS:
            row["fresh_" + arm] = copy.deepcopy(row[arm])
        result = {"evidence": {"cpu_raw": raw}}
        runs, repros = cpu._normalize("repeat_in_process", result, ctx)
        self.assertEqual(len(runs), 8)
        self.assertFalse(repros)
        row["fresh_candidate"][0]["diagnostics"]["execution_affinity"] = [1]
        with self.assertRaises(cpu.InvalidEvidence):
            cpu._normalize("repeat_in_process", result, ctx)

    def test_docker_command_pins_physical_mask_and_cleans_on_deadline(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/runner"))
        import docker_executor
        from runner import Context, Step
        scratch = self.root / "scratch"
        scratch.mkdir()
        ctx = Context(scratch, self.root, self.root, self.root, self.root, docker=True)
        executor = docker_executor.DockerExecutor(IMAGE, cpuset="0,1")
        with patch.object(docker_executor.subprocess, "run", side_effect=[
                cpu.SubsetTimeout("unit deadline"), subprocess.CompletedProcess([], 0, b"", b"")]) as run:
            with self.assertRaises(cpu.SubsetTimeout):
                executor.run([Step(["true"])], ctx, {})
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[command.index("--cpuset-cpus") + 1], "0,1")
        cleanup = run.call_args_list[1]
        self.assertEqual(cleanup.args[0][:3], ["docker", "rm", "-f"])
        self.assertEqual(cleanup.kwargs["timeout"], 15)


class RetainedSQLFluffPolicyTests(unittest.TestCase):
    def replay(self, mutation=None, remove_spec=False, stdout_cases_unsupported_readonly=False):
        from judge.tests import test_fuzzing_io as fixtures
        from judge import fuzzing_io
        fixture = fixtures.SharedIOPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if stdout_cases_unsupported_readonly:
            # numpy-quantile shape: a file-output case earns readonly_output credit, so the task
            # supplies no task-wide applicability evidence, and the runner reports readonly_dir
            # as unsupported for the stdout-only cases.
            fixture.request.pop("readonly_applicability_evidence")
        repro = json.loads((Path(__file__).parent / "fixtures/sqlfluff_cpu_classifier_repro.json").read_text())
        fixture.specs[0].update({key: repro[key] for key in ("watch_inputs", "output_dirs", "required_outputs")})
        fixture.specs[0]["watch_outputs"] = ["project"]
        def run(ctx):
            ctx["hooks"] = fixture.hooks
            def pair(**kwargs):
                raw = fixture.pair(**kwargs)
                index = int(kwargs["out"].name.split("-")[-1])
                for fault, arms in raw["observations"].items():
                    for arm_index, arm in enumerate(cpu.ARMS):
                        row = arms[arm]
                        row.update(execution_affinity=ctx["cpu_requested"], observer_sha256="d" * 64)
                        if kwargs["output_dirs"]:
                            row["output_before"] = {"project/case.fixed.sql": {"kind": "missing"}}
                            row["required_outputs"] = {"project/case.fixed.sql":
                                {"kind": "file", "bytes": 10, "sha256": "e" * 64}
                                if fault == "healthy" else {"kind": "missing"}}
                            if fault == "readonly_dir":
                                row["stderr"] = fixtures.stream(repro["stderr_template"].format(
                                    path=repro["paths"][index][arm_index]).encode())
                        elif fault == "readonly_dir" and stdout_cases_unsupported_readonly:
                            row.clear()
                            row.update(status="unsupported", reason="readonly_dir needs declared output directories",
                                       execution_affinity=ctx["cpu_requested"], observer_sha256="d" * 64)
                        if arm == "candidate" and mutation:
                            mutation(row, fault, fixtures.stream)
                return raw
            with patch("tools.fuzz_runners.cli_faults.run_pair", side_effect=pair):
                result = fuzzing_io.cli_gate(ctx)
            if remove_spec:
                for case in result["evidence"]["cases"]:
                    case.pop("comparison_spec")
            return result
        runners = {name: lambda _: {"status": "not_applicable", "applicability_evidence": {"unit": True}}
                   for name in cpu.GATES}
        runners["cli_faults"] = run
        return cpu.check(fixture.task, fixture.root / "b", fixture.root / "c", IMAGE,
                         fixture.root / "proof", builds=BUILDS, runners=runners)

    def test_retained_pristine_temp_names_pass_all_subsets(self):
        self.assertTrue(self.replay()["passed"])

    def test_errno_and_user_visible_path_changes_remain_regressions(self):
        import base64
        for old, new in ((b"[Errno 30]", b"[Errno 13]"),
                         (b"project/case.fixed.sql", b"other/user.sql")):
            def mutate(row, fault, stream):
                if fault == "readonly_dir":
                    row["stderr"] = stream(base64.b64decode(row["stderr"]["head_base64"]).replace(old, new))
            with self.subTest(new=new):
                self.assertEqual(self.replay(mutate)["status"], "contract_regression")

    def test_missing_path_role_evidence_is_unmeasured(self):
        self.assertEqual(self.replay(remove_spec=True)["status"], "unmeasured")

    def test_stdout_only_cases_need_no_task_wide_readonly_evidence(self):
        result = self.replay(stdout_cases_unsupported_readonly=True)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(row["affinity_verified"] for row in result["records"]))

    def test_stdout_only_readonly_still_requires_both_arms_unsupported(self):
        def mutate(row, fault, stream):
            if fault == "readonly_dir" and row.get("status") == "unsupported":
                row["status"] = "observed"
        self.assertEqual(self.replay(mutate, stdout_cases_unsupported_readonly=True)["status"], "unmeasured")


class RealTaskDockerTests(unittest.TestCase):
    def test_existing_task_cpu_subsets(self):
        if not shutil.which("docker"):
            self.skipTest("Docker client unavailable")
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        if info.returncode:
            self.skipTest("Docker daemon unavailable (no accessible docker.sock); run tools/prove_cpu_correctness.sh on judge host")
        names = ("TASK", "BASELINE", "CANDIDATE", "IMAGE")
        if any(not os.environ.get("CPU_PROOF_" + name) for name in names):
            self.skipTest("Set CPU_PROOF_TASK/BASELINE/CANDIDATE/IMAGE to an admitted real task; no invented integration corpus")
        with tempfile.TemporaryDirectory() as folder:
            result = cpu.check(*(os.environ["CPU_PROOF_" + name] for name in names),
                Path(folder) / "proof", public_inputs=os.environ.get("CPU_PROOF_PUBLIC_INPUTS"),
                projects=os.environ.get("CPU_PROOF_PROJECTS"))
            self.assertTrue(result["passed"], json.dumps(result, indent=2))
            for row in result["records"]:
                self.assertEqual(row["actual_cpus"], list(range(row["cpu_count"])))


if __name__ == "__main__":
    unittest.main()
