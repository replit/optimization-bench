"""Uniform generated instructions and measured cgroup CPU boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tools/runner")]
import runner
import shim
import update_catalog
from docker_executor import DockerExecutor


class CpuPolicyTests(unittest.TestCase):
    def test_all_catalog_tasks_share_policy_without_legacy_metadata_bans(self):
        for task in (ROOT / "tasks").iterdir():
            if not (task / "task.json").is_file():
                continue
            text = (task / "instruction.md").read_text()
            if task.name in update_catalog.neutral_prompt_tasks():
                update_catalog.validate_neutral_prompt(task)
                self.assertNotIn(update_catalog.CPU_POLICY, text)
            else:
                self.assertEqual(text.count(update_catalog.CPU_POLICY), 1, task.name)
            self.assertNotIn("Forbidden: threads", text, task.name)
            contract = json.loads((task / "task.json").read_text()).get("contract", "")
            self.assertNotIn("no threads", contract, task.name)
            self.assertNotIn(",threads,", contract, task.name)
            self.assertNotIn("the timed CPU is pinned.",
                             json.loads((task / "task.json").read_text()).get("limits", ""), task.name)

    def test_uniform_generated_rule_migrates_legacy_clauses_idempotently(self):
        for legacy in ("Forbidden: threads; caching across calls",
                       "Forbidden: threads or other concurrency in the decode path; caching across calls",
                       "persistent result cache, new threads or global state changes.",
                       "one thread; native candidate threads may use the assigned core budget. The"):
            with tempfile.TemporaryDirectory() as directory:
                task = Path(directory)
                (task / "task.toml").write_text("[agent]\ntimeout_sec=7200\n")
                (task / "instruction.md").write_text(legacy)
                value = update_catalog.agent_instructions(task)
                self.assertEqual(value.count(update_catalog.CPU_POLICY), 1)
                self.assertNotIn("Forbidden: threads", value)
                self.assertNotIn("new threads", value)
                self.assertIn("BLACK_NUM_WORKERS", value)
                self.assertIn("No worker processes or threads may outlive", value)
                self.assertIn("Caching across calls", value)
                (task / "instruction.md").write_text(value)
                self.assertEqual(update_catalog.agent_instructions(task), value)

    def test_cpu_delta_is_fail_closed(self):
        self.assertEqual(shim.cpu_delta(100, 400), 300)
        self.assertIsNone(shim.cpu_delta(None, 400))
        self.assertIsNone(shim.cpu_delta(500, 400))

    def test_resident_cpu_callbacks_exclude_setup_and_export(self):
        events = []
        child = SimpleNamespace(pid=123, returncode=0, wait=lambda **_: events.append("export") or 0)
        with patch.object(runner, "wait_event", side_effect=[runner.signal.SIGSTOP] * 2), \
             patch.object(runner.os, "kill", side_effect=lambda *args: events.append("continue")):
            result = runner.execute_child(child, 2, resident=True,
                before_export=lambda: events.append("freeze"),
                measurement_start=lambda: events.append("cpu-start"),
                measurement_end=lambda: events.append("cpu-end"))
        self.assertEqual(result[3], "complete")
        self.assertEqual(events, ["cpu-start", "continue", "cpu-end", "freeze", "continue", "export"])

    @unittest.skipUnless(os.environ.get("RESIDENT_TEST_IMAGE"), "requires pinned Docker fixture")
    def test_real_resident_cpu_excludes_untimed_setup_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("build", "scratch", "inputs", "workload"):
                (root / name).mkdir()
            (root / "build/run.py").write_text(
                "import os,signal,time,threading,subprocess,sys\n"
                "def burn(seconds):\n"
                " end=time.process_time()+seconds\n"
                " while time.process_time()<end: pass\n"
                "burn(.2)\nos.kill(os.getpid(),signal.SIGSTOP)\n"
                "threads=[threading.Thread(target=burn,args=(.1,)) for _ in range(2)]\n"
                "for t in threads:t.start()\n"
                "for t in threads:t.join()\n"
                "subprocess.run([sys.executable,'-c',"
                "'import time;end=time.process_time()+.1\\nwhile time.process_time()<end:pass'],check=True)\n"
                "os.kill(os.getpid(),signal.SIGSTOP)\nburn(.2)\n")
            context = runner.Context(root / "scratch", root / "build", root / "inputs", root,
                                     root / "workload", docker=True)
            result = DockerExecutor(os.environ["RESIDENT_TEST_IMAGE"]).run([
                runner.Step(["python3", "/app/build/run.py"], timeout=15, resident=True)], context, {})[0]
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.leftovers, [])
            measured = result.telemetry["operation_cpu_time_usec"]
            lifetime = dict(line.split() for line in result.telemetry["cpu.stat"].splitlines())
            self.assertGreater(measured, 150000)
            self.assertGreater(int(lifetime["usage_usec"]) - measured, 300000)

    @unittest.skipUnless(os.environ.get("RESIDENT_TEST_IMAGE"), "requires pinned Docker fixture")
    def test_real_resident_thread_must_finish_before_result_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("build", "scratch", "inputs", "workload"):
                (root / name).mkdir()
            (root / "build/run.py").write_text(
                "import os,signal,threading,time\n"
                "os.kill(os.getpid(),signal.SIGSTOP)\n"
                "t=threading.Thread(target=lambda:time.sleep(60),daemon=True)\n"
                "t.start()\nos.kill(os.getpid(),signal.SIGSTOP)\n")
            context = runner.Context(root / "scratch", root / "build", root / "inputs", root,
                                     root / "workload", docker=True)
            result = DockerExecutor(os.environ["RESIDENT_TEST_IMAGE"]).run([
                runner.Step(["python3", "/app/build/run.py"], timeout=15, resident=True)], context, {})[0]
            self.assertTrue(any(row.get("kind") == "thread" for row in result.leftovers), result.leftovers)
