import copy
from contextlib import ExitStack, nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import heldout, process_state, reference, verifier
from judge.leftovers import classification, resident_workers
from runner import Case, Step
from tools import qualify_aa
import shim


def helper(pid, fd):
    code = f"from multiprocessing.resource_tracker import main;main({fd})"
    return {"pid": pid, "state": "T", "threads": 1,
            "ipc_fds": [fd],
            "argv": ["/usr/local/bin/python3", "-B", "-c", code],
            "cmdline": "/usr/local/bin/python3 -B -c " + code}


class LeftoverIdentityTests(unittest.TestCase):
    def test_pid_and_descriptor_differences_are_not_module_identity_changes(self):
        left, right = helper(10, 3), helper(999, 8)
        self.assertEqual(classification([SimpleNamespace(leftovers=[left])]),
                         classification([SimpleNamespace(leftovers=[right])]))
        del right["argv"]  # Older StepResult records retain the flattened command.
        self.assertEqual(classification([SimpleNamespace(leftovers=[left])]),
                         classification([SimpleNamespace(leftovers=[right])]))

    def test_extra_or_different_command_is_not_pristine_parity(self):
        baseline = classification([SimpleNamespace(leftovers=[helper(10, 3)])])
        self.assertNotEqual(baseline, classification([
            SimpleNamespace(leftovers=[helper(11, 3), helper(12, 5)])]))
        unrelated = {"pid": 11, "state": "T", "threads": 1,
                     "argv": ["/usr/local/bin/python3", "-m", "http.server", "0"]}
        self.assertNotEqual(baseline, classification([SimpleNamespace(leftovers=[unrelated])]))

    def test_non_descriptor_numeric_arguments_are_not_erased(self):
        left = {"argv": ["python3", "-c", "import time;time.sleep(60)"], "ipc_fds": [4]}
        right = {"argv": ["python3", "-c", "import time;time.sleep(90)"], "ipc_fds": [5]}
        self.assertNotEqual(classification([SimpleNamespace(leftovers=[left])]),
                            classification([SimpleNamespace(leftovers=[right])]))

    def test_zombies_are_dead_but_an_unknown_live_command_is_not_exempt(self):
        self.assertEqual(classification([SimpleNamespace(leftovers=[
            {"pid": 1, "state": "Z", "cmdline": ""}, {"pid": 2, "state": "X"}])]), [])
        with self.assertRaisesRegex(ValueError, "no command identity"):
            classification([SimpleNamespace(leftovers=[{"pid": 3, "state": "T", "cmdline": ""}])])

    def test_shared_snapshot_rechecks_state_after_reading_command(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder) / "8"
            (proc / "task/8").mkdir(parents=True)
            (proc / "cmdline").write_bytes(b"python3\0-m\0example\0")
            with patch.object(Path, "read_text", side_effect=["8 (python3) T 1", "8 (python3) Z 1"]), \
                    patch.object(shim, "thread_identities", return_value=[{"tid": 8}]):
                self.assertIsNone(shim.leftover_snapshot(proc))
            with patch.object(Path, "read_text", side_effect=["8 (python3) T 1", "8 (python3) T 1"]), \
                    patch.object(shim, "thread_identities", return_value=[{"tid": 8}]):
                value = shim.leftover_snapshot(proc)
                self.assertEqual(value["argv"], ["python3", "-m", "example"])
                self.assertEqual(value["state"], "T")
                self.assertEqual(value["threads"], 1)
            (proc / "cmdline").write_bytes(b"python3\0-m\0example\0\0")
            with patch.object(Path, "read_text", side_effect=["8 (python3) T 1", "8 (python3) T 1"]), \
                    patch.object(shim, "thread_identities", return_value=[{"tid": 8}]):
                self.assertEqual(shim.leftover_snapshot(proc)["argv"], ["python3", "-m", "example", ""])


class NativeWorkerIdentityTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "subprocess.run", "os.system", "os.fork", "os.kill",
                       "socket.create_connection", "socket.socket.connect", "urllib.request.urlopen"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))
        self.raw = (Path(__file__).with_name("fixtures") / "resident-rule-d-threads.json").read_bytes()
        self.rows = json.loads(self.raw)["failure_context"]["steps"][0]["leftovers"]

    def classify(self, rows):
        return classification([SimpleNamespace(leftovers=rows)])

    def test_canonical_incident_fixture_is_unchanged(self):
        self.assertEqual(len(self.raw), 3556)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),
                         "9012a9037aff5908fc66324ca26cd29584ac67b10f6346fb6c7cd9471c8941e8")
        self.assertEqual([row["tid"] for row in self.rows], [38, 40, 41])

    def test_each_actual_thread_and_combined_triple_have_typed_classification(self):
        for rows in ([row] for row in self.rows):
            self.assertEqual(self.classify(rows), [{"step": 0, "kind": "thread", "threads": 1, "count": 1}])
        self.assertEqual(self.classify(self.rows),
                         [{"step": 0, "kind": "thread", "threads": 1, "count": 3}])
        self.assertTrue(all("argv" not in row and "cmdline" not in row for row in self.rows))

    def test_actual_rows_round_trip_through_frozen_worker_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "8").mkdir()
            identities = [copy.deepcopy(row["thread_identities"][0]) for row in self.rows]
            main = {**identities[0], "tid": 8}
            with patch.object(shim, "Path", return_value=proc), \
                    patch.object(shim, "thread_identities", return_value=[main, *identities]):
                rows = list(shim.worker_tasks(8).values())
            for row in rows:
                row.update(step_index=0, observation_boundary="resident-result-stop")
        self.assertEqual(rows, self.rows)
        self.assertEqual(self.classify(rows),
                         [{"step": 0, "kind": "thread", "threads": 1, "count": 3}])

    def test_names_and_runtime_pid_values_are_not_commands_or_exemptions(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["name"] = row["thread_identities"][0]["name"] = "resource_tracker"
            for field in ("pid", "tid", "process_pid", "tgid", "start_ticks"):
                row[field] += 100
            row["thread_identities"][0].update(
                {field: row[field] for field in ("tid", "tgid", "start_ticks")})
        self.assertEqual(self.classify(rows), self.classify(self.rows))
        self.assertNotIn("command", self.classify(rows)[0])

    def test_operation_created_process_leader_is_typed_without_invented_argv(self):
        identity = {**self.rows[0]["thread_identities"][0], "tid": 50, "tgid": 50}
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "50").mkdir()
            with patch.object(shim, "Path", return_value=proc), \
                    patch.object(shim, "thread_identities", return_value=[identity]):
                row, = shim.worker_tasks(8).values()
            row.update(step_index=0, observation_boundary="resident-result-stop")
        self.assertNotIn("argv", row)
        self.assertNotIn("cmdline", row)
        self.assertEqual(self.classify([row]),
                         [{"step": 0, "kind": "process", "threads": 1, "count": 1}])

    def test_worker_identity_remains_bound_to_its_step(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["step_index"] = 1
        self.assertEqual(classification([SimpleNamespace(leftovers=[]), SimpleNamespace(leftovers=rows)]),
                         [{"step": 1, "kind": "thread", "threads": 1, "count": 3}])
        with self.assertRaisesRegex(ValueError, "Invalid resident Rule D worker identity"):
            self.classify(rows)

    def test_missing_or_contradictory_worker_identity_is_never_a_process_fallback(self):
        for field in ("kind", "pid", "process_pid", "tid", "tgid", "start_ticks", "state", "flags",
                      "sampled_monotonic_ns", "step_index", "threads", "thread_identities",
                      "observation_boundary", "reason"):
            with self.subTest(missing=field):
                row = copy.deepcopy(self.rows[0])
                del row[field]
                row["argv"] = ["not-evidence-of-a-native-thread"]
                with self.assertRaisesRegex(ValueError, "Invalid resident Rule D worker identity"):
                    self.classify([row])
        for field, value in (("kind", "unknown"), ("pid", 999), ("process_pid", 999),
                             ("tid", True), ("threads", None), ("step_index", 1),
                             ("state", None), ("flags", 4), ("thread_identities", [])):
            with self.subTest(field=field, value=value):
                row = copy.deepcopy(self.rows[0])
                row[field] = value
                with self.assertRaisesRegex(ValueError, "Invalid resident Rule D worker identity"):
                    self.classify([row])
        for field, value in (("tid", 999), ("tgid", 999), ("start_ticks", 999),
                             ("state", "S"), ("flags", False), ("sampled_monotonic_ns", 999)):
            with self.subTest(nested=field):
                row = copy.deepcopy(self.rows[0])
                row["thread_identities"][0][field] = value
                with self.assertRaisesRegex(ValueError, "Invalid resident Rule D worker identity"):
                    self.classify([row])

    def test_stopped_workers_are_not_dead_but_zombie_and_dead_rows_stay_excluded(self):
        self.assertEqual(self.classify(self.rows)[0]["count"], 3)
        for state in ("Z", "X"):
            rows = copy.deepcopy(self.rows)
            for row in rows:
                row["state"] = row["thread_identities"][0]["state"] = state
            self.assertEqual(self.classify(rows), [])

    def test_legacy_process_identity_stays_separate_and_raw_rows_are_not_mutated(self):
        original = copy.deepcopy(self.rows)
        legacy = helper(12, 3)
        result = self.classify([legacy, *self.rows])
        self.assertEqual(result[0], self.classify([legacy])[0])
        self.assertEqual(result[1], {"step": 0, "kind": "thread", "threads": 1, "count": 3})
        self.assertEqual(self.rows, original)

    def test_resident_only_check_preserves_legacy_process_classification(self):
        steps = [SimpleNamespace(leftovers=[helper(12, 3)])]
        self.assertEqual(resident_workers(steps), [])
        self.assertEqual(classification(steps)[0]["count"], 1)
        steps = [SimpleNamespace(leftovers=[{"pid": 12, "state": "T", "threads": 1}])]
        self.assertEqual(resident_workers(steps), [])
        with self.assertRaisesRegex(ValueError, "no command identity"):
            classification(steps)


class ResidentWorkerConsumerTests(unittest.TestCase):
    """Exercise host verdict wiring without workloads, probes or real samplers."""
    def setUp(self):
        for target in ("subprocess.Popen", "subprocess.run", "os.system", "os.fork", "os.kill",
                       "socket.create_connection", "socket.socket.connect", "urllib.request.urlopen"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / "fixture"
        self.workload = self.task / "environment/workload"
        self.inputs = self.workload / "inputs"
        for path in (self.inputs, self.task / "tests", self.root / "baseline", self.root / "candidate"):
            path.mkdir(parents=True)
        self.baseline, self.candidate = self.root / "baseline", self.root / "candidate"
        for folder in (self.baseline, self.candidate):
            (folder / "artifact").write_bytes(b"fixture")
        self.rows = json.loads((Path(__file__).with_name("fixtures") /
                                "resident-rule-d-threads.json").read_text()
                              )["failure_context"]["steps"][0]["leftovers"]
        self.data = {"case": b"synthetic matching result"}
        self.module = SimpleNamespace(
            cases=lambda _: [Case("case", {})], ENV={},
            references=lambda _: [{"path": "case", "expect_len": len(self.data["case"]),
                                   "expect_sha": hashlib.sha256(self.data["case"]).hexdigest()}],
            prepare=lambda *_: [Step(["/app/build/fixture"], resident=True)])
        self.settings = {"memory": "1g", "pids_limit": 64,
                         "minimum_pairs": 5, "maximum_pairs": 5, "warmups": 1,
                         "relative_ci_half_width": .02, "noise_thresholds": {}}
        (self.task / "tests/judge.json").write_text(json.dumps(self.settings))
        self.enterContext(patch.object(verifier, "load_task", return_value=self.module))
        self.enterContext(patch.object(heldout, "workload_view",
                                      side_effect=lambda *a, **k: nullcontext(self.workload)))

    def result(self, rows):
        return SimpleNamespace(exit_code=0, stdout=b"", stderr=b"", leftovers=copy.deepcopy(rows),
                               t0_ns=0, t1_ns=1, telemetry={"execution_protocol": "complete"})

    def test_reference_admission_rejects_actual_workers_in_both_scopes(self):
        for private in (False, True):
            for rows in ([], self.rows, [helper(12, 3)]):
                with self.subTest(private=private, workers=len(rows)), \
                        patch("runner.run_case", return_value=(self.data, [self.result(rows)])), \
                        patch("docker_executor.DockerExecutor"):
                    call = lambda: reference.outputs(
                        self.task, self.baseline, self.inputs, self.root, "image", "0-7", private)
                    if rows == self.rows:
                        with self.assertRaisesRegex(reference.QualificationError, "unfinished resident workers"):
                            call()
                    else:
                        self.assertTrue(call()["passed"])

    def test_measured_verifier_rejects_baseline_or_candidate_workers_without_sampling(self):
        execution = {"effective_cores": list(range(8)), "requested_cores": list(range(8)),
                     "available_cores": list(range(8)), "pinning_took_effect": True,
                     "host": {"boot_id_sha256": "fixture"}}
        for left, right in (([], []), (self.rows, []), ([], self.rows), (self.rows, self.rows),
                            ([helper(12, 3)], [helper(13, 8)])):
            def replay(module, case, ctx, executor):
                return self.data, [self.result(right if ctx.build == self.candidate else left)]
            with self.subTest(baseline_workers=left == self.rows, candidate_workers=right == self.rows), \
                    ExitStack() as stack:
                for target, value in (
                        ("judge.verifier.image_id", "image"),
                        ("judge.heldout.check", {"status": "unavailable"}),
                        ("judge.interfaces.check", {"passed": True}),
                        ("judge.runtime_state.review", {"runtime_configuration_review": []}),
                        ("judge.score_host.probe", {"host": {"boot_id_sha256": "fixture"}, "counters": {}}),
                        ("judge.telemetry.quality", {"noisy": False}),
                        ("judge.verifier.resources", {"cpu_time_usec": 1000, "peak_memory_bytes": 1000}),
                        ("judge.verifier.DockerExecutor", SimpleNamespace(case_time_ns=1)),
                        ("judge.verifier.timing_builds",
                         ({"baseline": [self.baseline], "candidate": [self.candidate]},
                          {}, self.root / "synthetic-timing"))):
                    stack.enter_context(patch(target, return_value=value))
                stack.enter_context(patch.object(verifier, "run_case", side_effect=replay))
                stack.enter_context(patch.object(verifier.scheduler, "session",
                                                side_effect=AssertionError("sampling forbidden")))
                call = lambda: verifier.evaluate(
                    self.task, self.baseline, self.candidate, self.root, "image", "0-7", "fixture",
                    plumbing=True, provisional=True, dry_run=True, execution_host=execution, seed=0,
                    behavior_receipt={"differential": {"passed": True}, "process_state": {"passed": True}})
                if left == self.rows:
                    with self.assertRaisesRegex(verifier.HarnessError, "Reference build.*unfinished resident workers"):
                        call()
                else:
                    result = call()
                    self.assertEqual(result["correct"], right != self.rows)
                    self.assertFalse(result["claimable"])
                    self.assertFalse(result["aa_passed"])
                    self.assertEqual(result["records"][-1]["resident_workers_finished"], right != self.rows)

    def test_qualification_observer_checks_workers_with_the_sampler_replaced(self):
        for rows in ([], self.rows):
            captured = []
            def no_sampling(units, measure, records, *args):
                captured.append(measure("case", "a"))
                raise ValueError("synthetic observer stop; no sampling")
            with self.subTest(workers=bool(rows)), ExitStack() as stack:
                for target, value in (
                        ("load_task", self.module), ("image_id", "image"), ("reference_identity", {}),
                        ("DockerExecutor", SimpleNamespace(case_time_ns=1)),
                        ("run_case", (self.data, [self.result(rows)])), ("resources", {})):
                    stack.enter_context(patch.object(qualify_aa, target, return_value=value))
                stack.enter_context(patch.object(qualify_aa.score_host, "select", return_value={
                    "pinning_took_effect": True, "requested_cores": list(range(8))}))
                stack.enter_context(patch.object(qualify_aa.telemetry, "fingerprint", return_value={
                    "model": "fixture", "kernel": "fixture", "online_cpu_count": 8}))
                stack.enter_context(patch.object(qualify_aa.telemetry, "counters", return_value={}))
                stack.enter_context(patch.object(qualify_aa.telemetry, "quality", return_value={"noisy": False}))
                stack.enter_context(patch.object(qualify_aa.telemetry, "text", return_value="fixture"))
                sampler = stack.enter_context(patch.object(qualify_aa, "sample_units", side_effect=no_sampling))
                result = qualify_aa.run(self.task, self.baseline, "image", "0-7", "public")
            sampler.assert_called_once()
            self.assertFalse(result["passed"])
            self.assertIsNone(result["candidate_verdict"])
            self.assertEqual(captured[0]["correct"], not rows)
            self.assertEqual(captured[0]["resident_workers_finished"], not rows)

    def test_lifecycle_probe_accounts_for_typed_rows_from_a_mock_executor(self):
        self.settings["process_state"] = {"enabled": True, "kind": "native"}
        (self.task / "tests/judge.json").write_text(json.dumps(self.settings))
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["step_index"] = 1
        with patch.object(process_state, "DockerExecutor") as executor, \
                patch.object(process_state, "validate_native"), \
                patch.object(verifier, "public_input_path", return_value=self.inputs):
            executor.return_value.run.return_value = [self.result([]), self.result(rows)]
            observed = process_state.probe(self.task, self.baseline, "image", "0-7")
        self.assertEqual(observed["rule_d_resident_workers"], 3)
        self.assertEqual(observed.rule_d_observations[1]["leftovers"], rows)

    def test_lifecycle_check_never_excuses_matching_resident_leaks(self):
        for left, right in ((0, 0), (3, 0), (0, 3), (3, 3)):
            with self.subTest(baseline=left, candidate=right), patch.object(
                    process_state, "probe", side_effect=[
                        {"rule_d_resident_workers": left}, {"rule_d_resident_workers": right}]):
                result = process_state.check(self.task, self.baseline, self.candidate, "image", "0-7")
            self.assertEqual(result["passed"], not (left or right))
            if left or right:
                self.assertEqual(result["resident_worker_violations"], {"baseline": left, "candidate": right})
