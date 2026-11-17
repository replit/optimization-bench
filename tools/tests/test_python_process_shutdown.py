import builtins
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
from docker_executor import DockerExecutor
from runner import Context, Step

STARTUP = ROOT / "tools/runner/python_startup"
SOURCE = (STARTUP / "sitecustomize.py").read_text()
MOUNT = f"{STARTUP}/sitecustomize.py:/judge-python/sitecustomize.py:ro"


class ShutdownTests(unittest.TestCase):
    def load(self):
        namespace = {}
        with patch("atexit.register") as register:
            exec(compile(SOURCE, str(STARTUP / "sitecustomize.py"), "exec"), namespace)
        register.assert_called_once_with(namespace["_shutdown"])
        return namespace["_shutdown"]

    def test_registration_does_not_eagerly_import_multiprocessing(self):
        imported = []
        original = builtins.__import__

        def observe(name, *args, **kwargs):
            imported.append(name)
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=observe):
            self.load()
        self.assertFalse(any(name.startswith("multiprocessing") for name in imported))

    def test_unloaded_services_are_not_initialized(self):
        shutdown = self.load()
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": None,
            "multiprocessing.resource_tracker": None,
        }):
            shutdown()

    def test_loaded_services_stop_in_descriptor_dependency_order(self):
        shutdown = self.load()
        calls = []
        server = SimpleNamespace(_forkserver_pid=101, _stop=lambda: calls.append("forkserver"))
        tracker = SimpleNamespace(_pid=102, _stop=lambda: calls.append("tracker"))
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            shutdown()
        self.assertEqual(calls, ["forkserver", "tracker"])

    def test_shutdown_failure_is_not_silenced_or_replaced_by_killing(self):
        shutdown = self.load()
        tracker = SimpleNamespace(_pid=102, _stop=Mock(side_effect=RuntimeError("cannot join")))
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": None,
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }), patch("os.kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "cannot join"):
                shutdown()
            kill.assert_not_called()

    def test_removed_owned_socket_after_join_does_not_skip_tracker(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=101, _forkserver_address="/owned.sock")
        tracker = SimpleNamespace(_pid=102, _stop=Mock())

        def stop():
            server._forkserver_pid = None
            raise FileNotFoundError(2, "removed by util finalizer", "/owned.sock")

        server._stop = stop
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            shutdown()
        tracker._stop.assert_called_once_with()

    def test_missing_socket_before_join_is_not_silenced(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=101, _forkserver_address="/owned.sock",
                                 _stop=Mock(side_effect=FileNotFoundError(2, "not joined", "/owned.sock")))
        tracker = SimpleNamespace(_pid=102, _stop=Mock())
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            with self.assertRaises(FileNotFoundError):
                shutdown()
        tracker._stop.assert_not_called()

    def test_unrelated_missing_file_after_join_is_not_silenced(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=101, _forkserver_address="/owned.sock")
        tracker = SimpleNamespace(_pid=102, _stop=Mock())

        def stop():
            server._forkserver_pid = None
            raise FileNotFoundError(2, "unrelated failure", "/other.sock")

        server._stop = stop
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            with self.assertRaises(FileNotFoundError):
                shutdown()
        tracker._stop.assert_not_called()

    def test_unidentified_missing_file_after_join_is_not_silenced(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=101, _forkserver_address=None)
        tracker = SimpleNamespace(_pid=102, _stop=Mock())

        def stop():
            server._forkserver_pid = None
            raise FileNotFoundError("unidentified failure")

        server._stop = stop
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            with self.assertRaises(FileNotFoundError):
                shutdown()
        tracker._stop.assert_not_called()

    def test_borrowed_and_already_stopped_services_are_not_owned(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=None, _stop=Mock())
        tracker = SimpleNamespace(_pid=None, _stop=Mock())
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }):
            shutdown()
        server._stop.assert_not_called()
        tracker._stop.assert_not_called()

    def test_inherited_callback_has_no_shutdown_authority(self):
        shutdown = self.load()
        server = SimpleNamespace(_forkserver_pid=101, _stop=Mock())
        tracker = SimpleNamespace(_pid=102, _stop=Mock())
        with patch.dict(sys.modules, {
            "multiprocessing.forkserver": SimpleNamespace(_forkserver=server),
            "multiprocessing.resource_tracker": SimpleNamespace(_resource_tracker=tracker),
        }), patch("os.getpid", return_value=os.getpid() + 1):
            shutdown()
        server._stop.assert_not_called()
        tracker._stop.assert_not_called()

    def test_real_startup_runs_application_finalizers_before_owned_services(self):
        program = """
import atexit,sys
from types import SimpleNamespace
sys.modules['multiprocessing.forkserver'] = SimpleNamespace(
    _forkserver=SimpleNamespace(_forkserver_pid=101, _stop=lambda: print('forkserver')))
sys.modules['multiprocessing.resource_tracker'] = SimpleNamespace(
    _resource_tracker=SimpleNamespace(_pid=102, _stop=lambda: print('tracker')))
atexit.register(lambda: print('application'))
print('body')
"""
        result = subprocess.run([sys.executable, "-c", program], capture_output=True,
                                env={**os.environ, "PYTHONPATH": str(STARTUP)}, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"body\napplication\nforkserver\ntracker\n")

    def test_real_fork_child_does_not_run_its_parents_callback(self):
        program = """
import os,sys
from types import SimpleNamespace
sys.modules['multiprocessing.forkserver'] = SimpleNamespace(
    _forkserver=SimpleNamespace(_forkserver_pid=101, _stop=lambda: print('forkserver')))
sys.modules['multiprocessing.resource_tracker'] = SimpleNamespace(
    _resource_tracker=SimpleNamespace(_pid=102, _stop=lambda: print('tracker')))
pid=os.fork()
if pid==0:
    print('child',flush=True)
    sys.exit(0)
os.waitpid(pid,0)
print('parent',flush=True)
"""
        result = subprocess.run([sys.executable, "-c", program], capture_output=True,
                                env={**os.environ, "PYTHONPATH": str(STARTUP)}, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"child\nparent\nforkserver\ntracker\n")

    def test_reference_helper_closure_authenticates_startup_source(self):
        from judge.reference import descriptor

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, trusted = root / "task", root / "trusted"
            task.mkdir()
            (trusted / "pristine").mkdir(parents=True)
            (trusted / "pristine/source.py").write_text("value=1\n")
            (trusted / "public-workload/inputs").mkdir(parents=True)
            (trusted / "public-workload/inputs/manifest.json").write_text("{}")
            (task / "release.json").write_text(json.dumps({"source_policy": {
                "source_roots": ["."], "allowed_files": ["source.py"],
                "allowed_extensions": [".py"], "protected_paths": [],
                "max_source_files": 5, "max_source_bytes": 1000,
            }}))
            value = descriptor(task, trusted, "test-image", public_only=True)
        self.assertEqual(value["helpers"]["tools/runner/python_startup/sitecustomize.py"],
                         hashlib.sha256((STARTUP / "sitecustomize.py").read_bytes()).hexdigest())

    def test_command_inventory_authenticates_implicit_startup_code(self):
        from tools import judge_command_audit

        path = "tools/runner/python_startup/sitecustomize.py"
        self.assertEqual(judge_command_audit.inventory()["source_sha256"][path],
                         hashlib.sha256((STARTUP / "sitecustomize.py").read_bytes()).hexdigest())


class ExecutorShutdownTransportTests(unittest.TestCase):
    def execute(self, executable="python3", resident=False, path=True, extra_path=""):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("scratch", "build", "inputs", "projects", "workload"):
                (root / name).mkdir()
            ctx = Context(*(root / name for name in
                            ("scratch", "build", "inputs", "projects", "workload")), docker=True)
            env = {"PYTHONPATH": str(root / "build/runtime") + extra_path} if path else {}
            step = Step([executable, "-c", "pass"], env=env, resident=resident)
            original_env, original_argv = dict(env), list(step.argv)
            observed = {}

            def docker(command, **kwargs):
                observed["command"] = command
                observed["request"] = json.loads((ctx.scratch / "steps.json").read_text())
                (ctx.scratch / "step-0.stdout").write_bytes(b"")
                (ctx.scratch / "step-0.stderr").write_bytes(b"")
                (ctx.scratch / "step-0.json").write_text(json.dumps({
                    "exit_code": 0, "t0_ns": 10, "t1_ns": 11, "leftovers": [], "telemetry": {},
                }))
                (ctx.scratch / "case-result.json").write_text('{"case_time_ns":1}')
                return SimpleNamespace(returncode=0, stderr=b"")

            with patch("docker_executor.subprocess.run", side_effect=docker):
                DockerExecutor("test-image", "0-7").run([step], ctx, {})
            self.assertEqual(observed["request"][0]["argv"], original_argv)
            self.assertEqual(step.argv, original_argv)
            self.assertEqual(observed["request"][0]["resident"], resident)
            self.assertEqual(step.env, original_env)
            return observed

    def test_nonresident_python_preserves_application_path_and_mounts_readonly_hook(self):
        observed = self.execute("/usr/local/bin/python3.14")
        self.assertEqual(observed["request"][0]["env"]["PYTHONPATH"],
                         "/judge-python:/app/build/runtime")
        self.assertIn(MOUNT, observed["command"])

    def test_existing_application_path_entries_keep_their_order(self):
        observed = self.execute(extra_path=":/app/workload:/usr/local/lib")
        self.assertEqual(observed["request"][0]["env"]["PYTHONPATH"],
                         "/judge-python:/app/build/runtime:/app/workload:/usr/local/lib")

    def test_python_without_application_path_gets_only_owned_startup_directory(self):
        observed = self.execute(path=False)
        self.assertEqual(observed["request"][0]["env"]["PYTHONPATH"], "/judge-python")

    def test_resident_api_scope_is_not_changed(self):
        observed = self.execute(resident=True)
        self.assertEqual(observed["request"][0]["env"]["PYTHONPATH"], "/app/build/runtime")
        self.assertNotIn(MOUNT, observed["command"])

    def test_nonpython_process_scope_is_not_changed(self):
        observed = self.execute("/app/build/bin/program")
        self.assertEqual(observed["request"][0]["env"]["PYTHONPATH"], "/app/build/runtime")
        self.assertNotIn(MOUNT, observed["command"])
