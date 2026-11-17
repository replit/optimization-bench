"""Regression coverage for relocation and normal observer-process shutdown."""
import json
import random
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from judge import differential, process_state
from judge.probe_build import mounts
from judge.probe_lifecycle import script_command

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = ROOT / "judge/probe_lifecycle.py"


class ProbeInfrastructureTests(unittest.TestCase):
    def test_differential_leftovers_keep_diagnostics_and_preparation_receipts(self):
        leftovers = [{"pid": 7, "cmdline": "unexpected-worker"}]
        preparation = {"duration_ns": 10, "timed": False}
        result = SimpleNamespace(exit_code=0, leftovers=leftovers,
                                 stdout=b"output", stderr=b"diagnostic")
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(differential, "DockerExecutor") as executor:
            task = Path(folder)
            (task / "tests").mkdir()
            (task / "tests/judge.json").write_text(json.dumps({
                "differential": {"command": ["python3", "/verifier-differential/run.py"]}}))
            executor.return_value.run.return_value = [result]
            executor.return_value.container_prepare = preparation
            observed = differential.execute(task, task, "image", "0", {}, 10)
        self.assertEqual(observed, {"exit_code": 0, "leftovers": 1,
                                   "leftover_processes": leftovers,
                                   "stdout": "output", "stderr": "diagnostic"})
        self.assertEqual(observed.receipts, {"container_prepare": preparation})
        self.assertNotIn("container_prepare", json.loads(json.dumps(observed)))

    def test_shared_driver_preserves_interpreter_script_and_arguments(self):
        self.assertEqual(
            script_command(["/usr/local/bin/python3.14", "/probe/run.py", "case"], "/driver.py"),
            ["/usr/local/bin/python3.14", "/driver.py", "/probe/run.py", "case"])
        self.assertEqual(script_command(["node", "/probe/run.cjs"], "/driver.py"),
                         ["node", "/probe/run.cjs"])
        with self.assertRaisesRegex(ValueError, "verifier script"):
            script_command(["python3", "-m", "example"], "/driver.py")

    def test_shared_driver_preserves_script_imports_argv_and_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sibling.py").write_text("value = 'unchanged'\n")
            script = root / "probe.py"
            script.write_text(
                "import multiprocessing as mp, sys\nfrom sibling import value\n"
                "lock = mp.get_context('spawn').RLock()\n"
                "assert sys.argv[1:] == ['argument']\nprint(value)\nraise SystemExit(7)\n")
            result = subprocess.run([sys.executable, str(LIFECYCLE), str(script), "argument"],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 7, result.stderr.decode())
            self.assertEqual(result.stdout, b"unchanged\n")
            self.assertEqual(result.stderr, b"")

    def test_build_aliases_rebind_both_build_time_roots_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            self.assertEqual(mounts(root), [
                "-v", f"{root}:/scratch/build:ro",
                "-v", f"{root}:/app/baseline-build:ro"])

    def test_pristine_failure_retains_case_and_process_evidence(self):
        observed = {"exit_code": 0, "leftovers": 1,
                    "leftover_processes": [{"pid": 7, "cmdline": "unexpected-worker"}]}
        task = ROOT / "tasks/sqlglot-mozilla-transpile"
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(differential, "execute", return_value=observed):
            with self.assertRaisesRegex(ValueError, "Pristine differential harness failed"):
                differential.check(task, "baseline", "candidate", "image", "0-7", folder, [1729])
            evidence = json.loads((Path(folder) / "pristine-error.json").read_text())
            self.assertEqual((evidence["seed"], evidence["index"]), (1729, 0))
            settings = json.loads((task / "tests/judge.json").read_text())["differential"]
            expected = next(differential.harness(task).generate(random.Random(1729), settings["case_budget"]))
            self.assertEqual(evidence["case"], expected)
            self.assertEqual(evidence["baseline"], observed)

    def test_multiprocessing_shutdown_joins_tracker_and_finalizes_live_locks(self):
        # Real spawn-context SemLock starts a real resource-tracker process.
        # Keeping the lock alive verifies finalizers run before tracker EOF;
        # otherwise cleanup emits leaked-semaphore/FileNotFoundError warnings.
        script = f"""
import multiprocessing as mp
from multiprocessing import resource_tracker
import os, runpy
lock = mp.get_context("spawn").RLock()
pid = resource_tracker._resource_tracker._pid
assert pid is not None
runpy.run_path({str(LIFECYCLE)!r})["finish_multiprocessing"]()
assert resource_tracker._resource_tracker._pid is None
try:
    os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError("tracker was not synchronously reaped")
print("joined")
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"joined\n")
        self.assertEqual(result.stderr, b"")

    def test_shutdown_does_not_kill_unmanaged_child(self):
        script = f"""
import runpy, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
try:
    runpy.run_path({str(LIFECYCLE)!r})["finish_multiprocessing"]()
    assert child.poll() is None
finally:
    child.terminate()
    child.wait()
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")

    @unittest.skipUnless(os.environ.get("RESIDENT_TEST_IMAGE"), "requires Docker probe test image")
    def test_real_unmanaged_child_is_still_caught_by_rule_d(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / "probe-task"
            tests = task / "tests"
            tests.mkdir(parents=True)
            (tests / "operation.py").write_text("def run(library):\n    library.run()\n")
            (tests / "judge.json").write_text(json.dumps({"process_state": {
                "enabled": True, "kind": "python", "representative_operation": {
                    "module": "example", "root": "/app/build/project", "script": "operation.py"}}}))
            for name, code in [
                    ("baseline", "def run():\n    pass\n"),
                    ("candidate", "import subprocess, sys\n"
                     "def run():\n    subprocess.Popen([sys.executable, '-c', "
                     "'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, "
                     "stderr=subprocess.DEVNULL)\n")]:
                project = root / name / "project"
                project.mkdir(parents=True)
                (project / "example.py").write_text(code)
            result = process_state.check(task, root / "baseline", root / "candidate",
                                         os.environ["RESIDENT_TEST_IMAGE"], "0")
            self.assertFalse(result["passed"])
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["baseline"]["rule_d_leftovers"], 0)
            self.assertEqual(result["candidate"]["rule_d_leftovers"], 1)
            self.assertIn("rule_d_leftovers", result["differences"])
