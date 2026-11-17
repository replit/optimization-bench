import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import calibration, heldout_check, scheduler, verifier, reference
from runner import Case, StepResult

IMAGE = os.environ.get("RESIDENT_TEST_IMAGE")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def make_task(root):
    task = root / "fixture-task"
    tests = task / "tests"
    inputs = tests / "heldout"
    data = b"held-out-only\n"
    sha = hashlib.sha256(data).hexdigest()
    (inputs / "files").mkdir(parents=True)
    (inputs / "files" / sha).write_bytes(data)
    manifest = {"splits": {"private": [{"docs": [{
        "path": "heldout-control-case", "sha256": sha, "bytes": len(data),
        "expect_sha": sha, "expect_len": len(data)}]}]}}
    write_json(inputs / "manifest.json", manifest)
    write_json(tests / "heldout.lock.json", {"schema_version": 1, "status": "available",
        "archives": [], "files": {"heldout/files/" + sha: sha,
        "heldout/manifest.json": hashlib.sha256((inputs / "manifest.json").read_bytes()).hexdigest()}})
    workload = task / "environment/workload"
    workload.mkdir(parents=True)
    (workload / "task.py").write_text("""
import shutil
from runner import Case, Step, documents
ENV = {}
def cases(inputs):
    return [Case(doc["path"], doc) for doc in documents(inputs)]
def prepare(case, ctx):
    shutil.copyfile(ctx.inputs / "files" / case.data["sha256"], ctx.scratch / "input.bin")
    code = ("import sys;from pathlib import Path;sys.path.insert(0,'/app/build');"
            "from marker import render;sys.stdout.buffer.write(render(Path('/scratch/input.bin').read_bytes()))")
    return [Step(["python3", "-c", code])]
def collect(case, ctx, results):
    return {case.id: results[0].stdout}
""")
    write_json(tests / "judge.json", {
        "memory": "512m", "pids_limit": 64, "noise_thresholds": {},
        "public_interface": {"kind": "binary", "reason": "Fixture exposes only its workload executable"},
        "source_path": "/app/heldout-fixture",
        "build_source_env": "OPTIMBENCH_SOURCE", "public_projects_required": False,
        "upstream_gate": {"format": "outcomes", "report": "upstream.json",
            "command": ["python3", "-c",
                "import sys,json;from pathlib import Path;sys.path.insert(0,'/scratch/source');"
                "import test_original;test_original.run();"
                "Path('/scratch/upstream.json').write_text(json.dumps("
                "{'passed':True,'outcomes':{'original-test':'passed'}}))"]}})
    write_json(task / "release.json", {"source_policy": {
        "source_roots": ["."], "allowed_files": ["marker.py"], "allowed_extensions": [".py"],
        "protected_paths": ["test_original.py"], "max_source_files": 5, "max_source_bytes": 10000}})
    public = workload / "inputs"
    (public / "files").mkdir(parents=True)
    public_data = b"public-only\n"
    public_sha = hashlib.sha256(public_data).hexdigest()
    (public / "files" / public_sha).write_bytes(public_data)
    write_json(public / "manifest.json", {"splits": {"public": [{"docs": [{
        "path": "public-case", "sha256": public_sha, "bytes": len(public_data),
        "expect_sha": public_sha, "expect_len": len(public_data)}]}]}})
    return task


class HeldoutCheckRefusalTests(unittest.TestCase):
    def test_unavailable_lock_is_refused_before_build_or_execution(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(heldout_check.build, "build") as build:
            root = Path(folder)
            task = make_task(root)
            write_json(task / "tests/heldout/manifest.json", {"splits": {}})
            path = task / "tests/heldout.lock.json"
            lock = json.loads(path.read_text())
            lock["status"] = "unavailable"
            lock["files"]["heldout/manifest.json"] = hashlib.sha256(
                (task / "tests/heldout/manifest.json").read_bytes()).hexdigest()
            write_json(path, lock)
            with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
                heldout_check.run(task, "image", "0-7", root / "work", root / "cache")
            build.assert_not_called()

    def test_unsmudged_pointer_is_refused_even_when_its_hash_is_pinned(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(heldout_check.build, "build") as build:
            root = Path(folder)
            task = make_task(root)
            body = next((task / "tests/heldout/files").iterdir())
            body.write_text("version https://git-lfs.github.com/spec/v1\n"
                            "oid sha256:" + "1" * 64 + "\nsize 10\n")
            lock = json.loads((task / "tests/heldout.lock.json").read_text())
            lock["files"][body.relative_to(task / "tests").as_posix()] = hashlib.sha256(body.read_bytes()).hexdigest()
            write_json(task / "tests/heldout.lock.json", lock)
            with self.assertRaisesRegex(ValueError, "LFS object not fetched"):
                heldout_check.run(task, "image", "0-7", root / "work", root / "cache")
            build.assert_not_called()


class RuleDFailureContextTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "subprocess.run", "os.system", "os.fork", "os.kill",
                       "socket.create_connection", "socket.socket.connect", "urllib.request.urlopen"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))

    def comparison(self, task, rows, negative_control=False, candidate_output=b"held-out-only\n",
                   telemetry_override=None):
        module = SimpleNamespace(cases=lambda inputs: [Case("heldout-control-case", {})])
        self.scratches = []

        def observed(module, case, ctx, executor):
            self.scratches.append(ctx.scratch)
            index = len(self.scratches) - 1
            telemetry = {"execution_protocol": "synthetic-recorded-protocol", "main_pid": 1000 + index,
                         "exit_observation": {"kind": "synthetic-recorded-exit", "status": 0},
                         "kernel_exiting_tasks": [{"tid": 90, "flags": 4}],
                         "env": {"FIXTURE_ONLY": "not-for-diagnostics"}}
            if telemetry_override is not None:
                telemetry = telemetry_override
            return {case.id: b"held-out-only\n" if index == 0 else candidate_output}, [
                StepResult(0, b"do-not-copy-stdout", b"do-not-copy-stderr",
                           leftovers=rows[index], telemetry=telemetry)]

        with patch.object(heldout_check.verifier, "load_task", return_value=module), \
             patch.object(heldout_check, "DockerExecutor", return_value=SimpleNamespace(container_prepare=None)), \
             patch.object(heldout_check, "run_case", side_effect=observed):
            return heldout_check.compare(task, task / "baseline", task / "candidate",
                                         task, "fixture-image", "0-7", negative_control=negative_control)

    def test_missing_worker_or_process_identity_remains_invalid_with_exact_failure_context(self):
        worker = {"pid": 42, "process_pid": 40, "tid": 42, "tgid": 40, "start_ticks": 123,
                  "state": "S", "flags": 0, "threads": 1, "thread_identities": [{"tid": 42, "start_ticks": 123}],
                  "kind": "thread", "reason": "worker outlived measured operation", "step_index": 0,
                  "observation_boundary": "resident-result-stop"}
        unknown = {"pid": 43, "state": None, "threads": None, "thread_identities": None,
                   "argv": [], "cmdline": "", "metadata_error": "PermissionError", "sampled_monotonic_ns": 1234}
        for row, arm, message in (
                (worker, "candidate", "Invalid resident Rule D worker identity"),
                (unknown, "baseline", "Live Rule D leftover has no command identity")):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task = make_task(root)
                output = root / "result.json"
                rows = [[], [row]] if arm == "candidate" else [[row]]
                with patch.object(heldout_check, "run",
                        side_effect=lambda *args: self.comparison(task, rows)), patch("sys.stdout", io.StringIO()):
                    code = heldout_check.main(["--task", str(task), "--image", "fixture-image",
                        "--cpuset", "0-7", "--output", str(output)])
                result = json.loads(output.read_text())
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "invalid run")
                self.assertEqual(result["harness_error"], message)
                self.assertFalse(result["correct"])
                context = result["failure_context"]
                self.assertEqual((context["case"], context["arm"], context["stage"]),
                                 ("heldout-control-case", arm, "rule-d-classification"))
                self.assertTrue(context["run_case_returned"])
                self.assertEqual(context["completed_record_count"], 1 if arm == "candidate" else 0)
                self.assertEqual(context["last_completed_record"],
                    {"case": "heldout-control-case", "build": "baseline", "matched": True}
                    if arm == "candidate" else None)
                self.assertEqual(context["steps"][0]["leftovers"], [row])
                self.assertEqual(context["steps"][0]["step"], 0)
                self.assertEqual(context["steps"][0]["exit_code"], 0)
                self.assertEqual(set(context["steps"][0]["telemetry"]),
                                 {"execution_protocol", "main_pid", "exit_observation", "kernel_exiting_tasks"})
                self.assertEqual(context["steps"][0]["telemetry"]["kernel_exiting_tasks"], [{"tid": 90, "flags": 4}])
                self.assertTrue(all(not scratch.exists() for scratch in self.scratches))
                serialized = json.dumps(context)
                for forbidden in ("do-not-copy-stdout", "do-not-copy-stderr", "not-for-diagnostics", "outputs"):
                    self.assertNotIn(forbidden, serialized)
                self.assertNotIn("records", result)
                self.assertNotIn("candidate_verdict", result)

    def test_actual_worker_rows_fail_baseline_or_candidate_completion_not_command_identity(self):
        fixture = json.loads((Path(__file__).with_name("fixtures") /
                              "resident-rule-d-threads.json").read_text())
        workers = fixture["failure_context"]["steps"][0]["leftovers"]
        for rows, status, failing_arms in (([workers, []], "invalid run", {"baseline"}),
                                           ([[], workers], "incorrect", {"candidate"}),
                                           ([workers, workers], "invalid run", {"baseline", "candidate"})):
            with self.subTest(status=status, failing_arms=failing_arms), \
                    tempfile.TemporaryDirectory() as temporary:
                task = make_task(Path(temporary))
                result = self.comparison(task, rows)
                self.assertEqual(result["status"], status)
                self.assertFalse(result["correct"])
                self.assertNotIn("harness_error", result)
                self.assertNotIn("failure_context", result)
                self.assertNotIn("candidate_verdict", result)
                self.assertEqual(len(result["records"]), 2)
                for record in result["records"]:
                    arm = record["build"]
                    self.assertTrue(record["outputs_matched"])
                    self.assertEqual(record["resident_workers_finished"], arm not in failing_arms)
                    if arm in failing_arms:
                        self.assertFalse(record["matched"])
                        self.assertEqual(record["leftover_classification"],
                                         [{"step": 0, "kind": "thread", "threads": 1, "count": 3}])
                        self.assertEqual(record["steps"][0]["leftovers"], workers)
                if len(failing_arms) == 2:
                    self.assertTrue(all(record["leftovers_matched"] for record in result["records"]))
                    self.assertFalse(any(record["matched"] for record in result["records"]))
                if "baseline" in failing_arms:
                    self.assertEqual(result["baseline_failed_cases"], ["heldout-control-case"])
                else:
                    self.assertEqual(result["baseline_failed_cases"], [])
                    self.assertEqual(result["failed_cases"], ["heldout-control-case"])

    def test_matching_operation_created_process_leaders_do_not_excuse_live_workers(self):
        worker = json.loads((Path(__file__).with_name("fixtures") /
                             "resident-rule-d-threads.json").read_text()
                            )["failure_context"]["steps"][0]["leftovers"][0]
        worker.update(kind="process", pid=50, process_pid=50, tid=50, tgid=50)
        worker["thread_identities"][0].update(tid=50, tgid=50)
        with tempfile.TemporaryDirectory() as temporary:
            result = self.comparison(make_task(Path(temporary)), [[worker], [worker]])
        self.assertEqual(result["status"], "invalid run")
        self.assertTrue(all(record["leftovers_matched"] for record in result["records"]))
        self.assertFalse(any(record["resident_workers_finished"] for record in result["records"]))
        self.assertFalse(any(record["matched"] for record in result["records"]))
        self.assertEqual(result["records"][0]["leftover_classification"],
                         [{"step": 0, "kind": "process", "threads": 1, "count": 1}])

    def test_negative_control_requires_completed_workers_even_with_pristine_parity(self):
        workers = json.loads((Path(__file__).with_name("fixtures") /
                              "resident-rule-d-threads.json").read_text()
                             )["failure_context"]["steps"][0]["leftovers"]
        with tempfile.TemporaryDirectory() as temporary:
            result = self.comparison(make_task(Path(temporary)), [workers, workers], negative_control=True)
        self.assertEqual(result["status"], "invalid control")
        self.assertFalse(result["control_original_matched"])
        self.assertFalse(result["control_passed"])
        self.assertIsNone(result["candidate_verdict"])
        self.assertTrue(all(record["leftovers_matched"] for record in result["records"]))

    def test_postexit_helper_comparison_still_rejects_extra_and_different_children(self):
        baseline = {"pid": 12, "state": "T", "argv": ["fixture-helper"], "threads": 1}
        same = {**baseline, "pid": 99}
        other = {**same, "argv": ["unrelated-child"]}
        for candidate, correct in (([same], True), ([same, same], False), ([other], False)):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as temporary:
                result = self.comparison(make_task(Path(temporary)), [[baseline], candidate])
            self.assertEqual(result["correct"], correct)
            self.assertEqual(result["status"], "heldout check passed" if correct else "incorrect")
            self.assertTrue(all(record["outputs_matched"] for record in result["records"]))
            self.assertTrue(all(record["resident_workers_finished"] for record in result["records"]))
            self.assertEqual(result["records"][1]["leftovers_matched"], correct)

    def test_successful_and_incorrect_output_verdicts_have_unchanged_records_without_failure_context(self):
        for candidate, status, correct in ((b"held-out-only\n", "heldout check passed", True),
                                            (b"different", "incorrect", False)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                task = make_task(Path(temporary))
                rows = [[{"pid": 12, "state": "S", "argv": ["fixture-helper"], "threads": 1}],
                        [{"pid": 99, "state": "S", "argv": ["fixture-helper"], "threads": 1}]]
                result = self.comparison(task, rows, candidate_output=candidate)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["correct"], correct)
                self.assertNotIn("failure_context", result)
                self.assertEqual(len(result["records"]), 2)
                for index, record in enumerate(result["records"]):
                    self.assertEqual(record["steps"], [{"exit_code": 0, "leftovers": rows[index]}])
                    self.assertEqual(record["leftover_classification"],
                                     [{"step": 0, "command": ["fixture-helper"], "threads": 1, "count": 1}])
                self.assertEqual(result["records"][1]["outputs_matched"], correct)

    def test_missing_and_null_lifecycle_observations_remain_unavailable_not_dead(self):
        unavailable = {key: None for key in
                       ("execution_protocol", "main_pid", "exit_observation", "kernel_exiting_tasks")}
        row = {"pid": 43, "state": None, "argv": [], "cmdline": "", "metadata_error": "PermissionError"}
        for telemetry in ({}, unavailable):
            with self.subTest(telemetry=telemetry), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task = make_task(root)
                output = root / "result.json"
                with patch.object(heldout_check, "run", side_effect=lambda *args:
                        self.comparison(task, [[row]], telemetry_override=telemetry)), \
                        patch("sys.stdout", io.StringIO()):
                    code = heldout_check.main(["--task", str(task), "--image", "fixture-image",
                        "--cpuset", "0-7", "--output", str(output)])
                result = json.loads(output.read_text())
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "invalid run")
                self.assertEqual(result["harness_error"], "Live Rule D leftover has no command identity")
                self.assertEqual(result["failure_context"]["steps"][0]["leftovers"], [row])
                self.assertEqual(result["failure_context"]["steps"][0]["telemetry"], unavailable)
                self.assertNotIn("records", result)
                for forbidden in ("do-not-copy-stdout", "do-not-copy-stderr", "not-for-diagnostics"):
                    self.assertNotIn(forbidden, output.read_text())

    def test_missing_identity_during_negative_control_is_still_invalid_not_a_successful_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = make_task(root)
            output = root / "result.json"
            rows = [[], [{"state": "R", "argv": [""], "threads": 1}]]
            with patch.object(heldout_check, "run",
                    side_effect=lambda *args: self.comparison(task, rows, negative_control=True)), \
                    patch("sys.stdout", io.StringIO()):
                code = heldout_check.main(["--task", str(task), "--image", "fixture-image",
                    "--cpuset", "0-7", "--output", str(output), "--negative-control"])
            result = json.loads(output.read_text())
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "invalid run")
            self.assertFalse(result["control_passed"])
            self.assertIsNone(result["candidate_verdict"])
            self.assertEqual(result["failure_context"]["arm"], "candidate")
            self.assertEqual(result["harness_error"], "Live Rule D leftover has no command identity")


class HeldoutCLIRejectionTests(unittest.TestCase):
    def test_reference_upstream_failure_and_real_infrastructure_failure_remain_invalid(self):
        errors = (reference.QualificationError("pristine upstream failed"),
                  heldout_check.HarnessError("container startup failed"))
        for error in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "result.json"
                with patch.object(heldout_check, "run", side_effect=error), patch("sys.stdout", io.StringIO()):
                    code = heldout_check.main(["--task", temporary, "--image", "image",
                        "--cpuset", "0-7", "--output", str(output)])
                result = json.loads(output.read_text())
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "invalid run")
                self.assertFalse(result["claimable"])
                self.assertNotIn("upstream_gate", result)
                self.assertNotIn("records", result)
                self.assertFalse(list(Path(temporary).glob("*-upstream-rejection.json")))

    def test_actual_candidate_upstream_rejection_is_incorrect_and_retains_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result.json"
            receipts = {"candidate": {"passed": False}, "baseline": {"passed": True}}
            error = heldout_check.gates.UpstreamRejected("Upstream command failed", receipts)
            with patch.object(heldout_check, "run", side_effect=error), patch("sys.stdout", io.StringIO()):
                code = heldout_check.main(["--task", str(root / "task"), "--image", "image",
                    "--cpuset", "0-7", "--output", str(output)])
            self.assertEqual(code, 1)
            result = json.loads(output.read_text())
            self.assertEqual(result["status"], "incorrect")
            self.assertEqual(result["upstream_gate"], "rejected")
            self.assertFalse(result["correct"])
            self.assertFalse(result["timing_performed"])
            self.assertNotIn("records", result)
            self.assertEqual(json.loads(Path(result["upstream_rejection"]).read_text()), receipts)

    def test_upstream_failure_never_counts_as_a_successful_negative_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            error = heldout_check.gates.UpstreamRejected("rejected", {})
            with patch.object(heldout_check, "run", side_effect=error), patch("sys.stdout", io.StringIO()):
                code = heldout_check.main(["--task", temporary, "--image", "image",
                    "--cpuset", "0-7", "--output", str(output), "--negative-control"])
            result = json.loads(output.read_text())
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "invalid control")
            self.assertFalse(result["control_passed"])
            self.assertIsNone(result["candidate_verdict"])


@unittest.skipUnless(IMAGE, "requires Docker correctness-test image")
class HeldoutCheckDockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.task = make_task(cls.root)
        cls.source = cls.root / "source"
        cls.source.mkdir()
        (cls.source / "marker.py").write_text("def render(raw):\n    return raw\n")
        (cls.source / "test_original.py").write_text(
            "import marker\ndef run():\n    assert callable(marker.render)\n")
        (cls.root / "build.sh").write_text(
            '#!/bin/sh\nset -eu\nOUT=$1\nmkdir -p "$OUT"\n'
            'cp "${OPTIMBENCH_SOURCE:-/app/heldout-fixture}/marker.py" "$OUT/marker.py"\n')
        cls.hook_script = b"""#!/usr/bin/env python3
import json,sys
from pathlib import Path
request=Path(sys.argv[1])
assert json.loads(request.read_text())['steps']
try:request.write_text('bad')
except OSError:pass
else:raise RuntimeError('writable trusted request')
counter=Path('/scratch/prepare-count')
assert not counter.exists()
counter.write_text('1')
print('prepared once')
"""
        (cls.root / "prepare.py").write_bytes(cls.hook_script)
        (cls.root / "prepare.py").chmod(0o755)
        (cls.root / "Dockerfile").write_text(
            f"FROM {IMAGE}\nCOPY source/ /app/heldout-fixture/\n"
            "COPY prepare.py /app/workload/guards/prepare.py\n"
            "COPY build.sh /app/workload/build.sh\n"
            "RUN rm -rf /app/workload/inputs\n"
            "COPY fixture-task/environment/workload/inputs/ /app/workload/inputs/\n"
            "RUN rm -rf /app/baseline-build && sh /app/workload/build.sh /app/baseline-build\n")
        cls.image = "heldout-check-test:" + str(os.getpid())
        result = subprocess.run(["docker", "build", "--network=none", "-t", cls.image, str(cls.root)],
                                capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode()[-3000:])
        cls.cache = cls.root / "cache"

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "image", "rm", cls.image], capture_output=True)
        cls.temporary.cleanup()

    def run_check(self, name, source=None, negative_control=False):
        with patch.object(scheduler, "session", side_effect=AssertionError("timing forbidden")), \
             patch.object(calibration, "require", side_effect=AssertionError("calibration forbidden")):
            return heldout_check.run(self.task, self.image, "0-7",
                                     self.root / name, self.cache, source=source,
                                     negative_control=negative_control)

    def helper_comparison(self, name, candidate_helpers=1, unrelated=False, control=False):
        builds = {}
        for arm, count in (("baseline", 1), ("candidate", candidate_helpers)):
            folder = self.root / (name + "-" + arm)
            folder.mkdir()
            command = (["-m", "http.server", "0"] if unrelated and arm == "candidate"
                       else ["-c", "import time; time.sleep(60)"])
            (folder / "marker.py").write_text(
                "import subprocess,sys\n"
                "def render(raw):\n"
                f"    for _ in range({count}):\n"
                f"        subprocess.Popen([sys.executable]+{command!r}, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "    return raw\n")
            builds[arm] = folder
        return heldout_check.compare(self.task, builds["baseline"], builds["candidate"],
                                     self.root, self.image, "0-7", negative_control=control)

    def test_pristine_identical_helpers_are_recorded_not_an_output_failure(self):
        result = self.helper_comparison("identical-helpers")
        self.assertTrue(result["correct"])
        self.assertTrue(all(r["leftovers_matched"] for r in result["records"]))
        self.assertTrue(all(r["steps"][0]["leftovers"] for r in result["records"]))

    def test_extra_candidate_helper_fails_despite_matching_outputs(self):
        result = self.helper_comparison("extra-helper", candidate_helpers=2)
        self.assertFalse(result["correct"])
        self.assertEqual(result["failed_cases"], ["heldout-control-case"])
        self.assertTrue(result["records"][1]["outputs_matched"])
        self.assertFalse(result["records"][1]["leftovers_matched"])

    def test_unrelated_candidate_helper_fails_despite_equal_process_count(self):
        result = self.helper_comparison("different-helper", unrelated=True)
        self.assertFalse(result["correct"])
        self.assertTrue(result["records"][1]["outputs_matched"])
        self.assertFalse(result["records"][1]["leftovers_matched"])

    def test_negative_control_accepts_original_matching_pristine_helpers(self):
        result = self.helper_comparison("control-with-helpers", control=True)
        self.assertTrue(result["control_original_matched"])
        self.assertTrue(result["control_passed"])
        self.assertEqual(result["status"], "negative control rejected")

    def test_supported_negative_control_passes_upstream_then_rejects_named_case(self):
        result = self.run_check("negative", negative_control=True)
        self.assertEqual(result["gates"]["upstream"], "passed")
        self.assertFalse(result["correct"])
        self.assertEqual(result["status"], "negative control rejected")
        self.assertTrue(result["control_passed"])
        self.assertTrue(result["control_original_matched"])
        self.assertTrue(result["is_control"])
        self.assertIsNone(result["candidate_verdict"])
        self.assertEqual(result["failed_cases"], ["heldout-control-case"])
        self.assertEqual(result["control_case"], "heldout-control-case")
        self.assertEqual(result["control_output"], "heldout-control-case")
        self.assertEqual({r["build"] for r in result["records"]}, {"baseline", "control"})

    def test_negative_control_is_invalid_if_unperturbed_output_was_already_wrong(self):
        trusted = heldout_check.score.assets(self.task, verifier.image_id(self.image), self.cache)
        wrong = self.root / "already-wrong-build"
        wrong.mkdir()
        (wrong / "marker.py").write_text("def render(raw):\n    return b'already wrong'\n")
        result = heldout_check.compare(self.task, trusted / "baseline", wrong,
            trusted / "projects", self.image, "0-7", negative_control=True)
        self.assertFalse(result["control_original_matched"])
        self.assertFalse(result["control_passed"])
        self.assertEqual(result["status"], "invalid control")
        self.assertIsNone(result["candidate_verdict"])

    def test_pristine_noop_build_upstream_and_heldout_exact_outputs_pass(self):
        result = self.run_check("noop")
        self.assertTrue(result["correct"])
        self.assertEqual(result["status"], "heldout check passed")
        self.assertFalse(result["public_only"])
        self.assertFalse(result["timing_performed"])
        self.assertFalse(result["aa_sampling_performed"])
        self.assertEqual(result["gates"]["upstream"], "passed")
        self.assertEqual({r["case"] for r in result["records"]}, {"heldout-control-case"})
        self.assertTrue(all(r["matched"] for r in result["records"]))
        self.assertTrue(all("ns" not in r for r in result["records"]))

    def test_fetch_only_input_runs_real_build_gate_and_docker_output_comparison(self):
        task = self.root / "fetch-only-fixture"
        shutil.copytree(self.task, task)
        lock_path = task / "tests/heldout.lock.json"
        lock = json.loads(lock_path.read_text())
        name = next(name for name in lock["files"] if name.startswith("heldout/files/"))
        body = (task / "tests" / name).read_bytes()
        (task / "tests" / name).unlink()
        lock["references"] = {name: {
            "kind": "file", "scope": "heldout", "role": "input",
            "license_basis": "fetch-only; no redistribution",
            "url": "https://example.org/immutable/fixture",
            "bytes": len(body), "sha256": lock["files"].pop(name)}}
        write_json(lock_path, lock)
        write_json(task / "payload.json", {"files": [
            {**entry, "name": "input", "destination": "tests/" + path, "retention": "fetch-only"}
            for path, entry in lock["references"].items()]})
        with patch("judge.heldout.urllib.request.urlopen", return_value=io.BytesIO(body)) as fetch, \
                patch.object(scheduler, "session", side_effect=AssertionError("timing forbidden")):
            result = heldout_check.run(task, self.image, "0-7",
                                      self.root / "fetch-only-work", self.cache)
        fetch.assert_called_once()
        self.assertTrue(result["correct"])
        self.assertEqual(result["gates"]["upstream"], "passed")
        self.assertFalse(result["timing_performed"])
        self.assertFalse((task / "tests" / name).exists())
        trusted = heldout_check.score.assets(task, verifier.image_id(self.image), self.cache)
        with patch("judge.heldout.urllib.request.urlopen") as fetch, \
                patch.object(verifier.interfaces, "check", return_value={"passed": True}):
            public = verifier.evaluate(task, trusted / "baseline", trusted / "baseline",
                trusted / "projects", self.image, "0-7", "fixture", plumbing=True,
                dry_run=True, behavior_receipt={"status": None})
        fetch.assert_not_called()
        self.assertTrue(public["correct"])
        self.assertEqual({r["case"] for r in public["records"]}, {"public-case"})

    def test_prepare_hook_is_identical_in_heldout_check_scoring_and_aa_qualification(self):
        task = self.root / "container-prepare-fixture"
        shutil.copytree(self.task, task)
        hook = task / "environment/workload/guards/prepare.py"
        hook.parent.mkdir()
        hook.write_bytes(self.hook_script)
        hook.chmod(0o755)
        write_json(task / "task.json", {"container_prepare":
            ["/app/workload/guards/prepare.py", "{steps}"]})
        work = self.root / "prepared-heldout-check"
        check = heldout_check.run(task, self.image, "0-7", work, self.cache)
        self.assertTrue(check["correct"])
        receipts = [record["container_prepare"] for record in check["records"]]
        trusted = heldout_check.score.assets(task, verifier.image_id(self.image), self.cache)
        calibration_path = self.root / "hook-calibration.json"
        rebuilt_reference = reference.ensure(task, trusted, self.image, "0-7", self.cache)
        write_json(calibration_path, {})
        with patch.object(verifier.interfaces, "check", return_value={"passed": True}), \
                patch.object(calibration, "require"):
            result = verifier.evaluate(task, rebuilt_reference["build"], work / "candidate/build",
                trusted / "projects", self.image, "0-7", "fixture",
                calibration_path=calibration_path, upstream_receipt=work / "upstream.json",
                source_sha256=check["source_sha256"], behavior_receipt={"status": None},
                warmups=1, minimum=5, maximum=5, reference_info=rebuilt_reference)
        self.assertTrue(result["correct"])
        self.assertEqual({row["arm"] for row in result["records"]}, {"aa", "ab"})
        for record in result["records"]:
            receipts.append(record["steps"][0]["telemetry"]["container_prepare"])
            self.assertEqual(record["ns"], sum(step["duration_ns"] for step in record["steps"]))
        self.assertTrue(all(receipt["exit_code"] == 0 and receipt["timed"] is False
                            for receipt in receipts))
        self.assertEqual({receipt["stdout_base64"] for receipt in receipts}, {"cHJlcGFyZWQgb25jZQo="})
        self.assertEqual({tuple(receipt["argv"]) for receipt in receipts},
                         {("/app/workload/guards/prepare.py", "/judge-steps.json")})

    def test_verifier_cli_heldout_mode_needs_no_calibration_or_candidate_arguments(self):
        output = self.root / "cli-result.json"
        result = subprocess.run([
            "python3", "-m", "judge.verifier", "--heldout-check",
            "--task", str(self.task), "--image", self.image, "--cpuset", "0-7",
            "--cache", str(self.cache), "--output", str(output)],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        self.assertTrue(report["correct"])
        self.assertFalse(report["calibration_required"])
        self.assertFalse(report["timing_performed"])
        self.assertEqual(report["mode"], "heldout-check")
        negative = self.root / "cli-negative.json"
        command = ["python3", "-m", "judge.verifier", "--heldout-check", "--negative-control",
                   "--task", str(self.task), "--image", self.image, "--cpuset", "0-7",
                   "--cache", str(self.cache), "--output", str(negative)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 1, result.stderr)
        control = json.loads(negative.read_text())
        self.assertTrue(control["control_passed"])
        self.assertIsNone(control["candidate_verdict"])
        self.assertEqual(control["failed_cases"], ["heldout-control-case"])

    def test_output_changing_control_fails_with_private_case_named(self):
        source = self.root / "control-source"
        shutil.copytree(self.source, source)
        (source / "marker.py").write_text("def render(raw):\n    return b'changed output'\n")
        result = self.run_check("control", source)
        self.assertFalse(result["correct"])
        self.assertEqual(result["status"], "incorrect")
        self.assertEqual(result["gates"]["upstream"], "passed")
        self.assertEqual(result["failed_cases"], ["heldout-control-case"])
        self.assertEqual(result["baseline_failed_cases"], [])
        self.assertFalse(result["records"][1]["outputs"]["heldout-control-case"]["matched"])

    def test_public_plumbing_dry_run_remains_public_with_available_private_lock(self):
        trusted = heldout_check.score.assets(self.task, verifier.image_id(self.image), self.cache)
        with patch.object(verifier.interfaces, "check", return_value={"passed": True}):
            result = verifier.evaluate(self.task, trusted / "baseline", trusted / "baseline",
                trusted / "projects", self.image, "0-7", "fixture", plumbing=True,
                dry_run=True, behavior_receipt={"status": None})
        self.assertTrue(result["correct"])
        self.assertEqual(result["scope"], "public-case plumbing check only")
        self.assertEqual(result["status"], "plumbing complete")
        self.assertEqual({r["case"] for r in result["records"]}, {"public-case"})
