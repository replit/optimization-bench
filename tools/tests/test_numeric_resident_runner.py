"""Numeric contracts and protected resident boundary regression tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
import runner as r
from docker_executor import DockerExecutor


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tasks" / name /
                                                   "environment/workload/task.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def npy(values, shape=None, dtype="<f8", fortran=False):
    shape = (len(values),) if shape is None else shape
    header = repr({"descr": dtype, "fortran_order": fortran, "shape": shape}).encode() + b"\n"
    header += b" " * (-(10 + len(header)) % 64)
    return r.bundle({"result.npy": b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) +
                     header + struct.pack("<" + ("d" if dtype == "<f8" else "f") * len(values), *values)})


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.numpy = load("numpy-quantile")

    def test_default_stays_exact(self):
        plain = types.SimpleNamespace()
        self.assertTrue(r.compare_results(plain, {"x": b"same"}, {"x": b"same"}))
        self.assertFalse(r.compare_results(plain, {"x": b"1"}, {"x": b"1.0"}))

    def test_numpy_different_hash_valid_numeric_output(self):
        a, b = npy([1.0]), npy([1.0 + 1e-15])
        self.assertNotEqual(hashlib.sha256(a).digest(), hashlib.sha256(b).digest())
        self.assertTrue(r.compare_results(self.numpy, {"x": a}, {"x": b}))
        self.assertFalse(r.compare_results(self.numpy, {"x": a}, {"x": npy([1.001])}))

    def test_numpy_shapes_dtypes_nan_inf_and_fortran(self):
        self.assertFalse(r.compare_results(self.numpy, {"x": npy([1.])}, {"x": npy([1.], ())}))
        self.assertFalse(r.compare_results(self.numpy, {"x": npy([1.])}, {"x": npy([1.], dtype="<f4")}))
        self.assertTrue(r.compare_results(self.numpy, {"x": npy([float("nan"), float("inf")])},
                                         {"x": npy([float("nan"), float("inf")])}))
        self.assertFalse(r.compare_results(self.numpy, {"x": npy([float("inf")])}, {"x": npy([1e300])}))
        self.assertTrue(r.compare_results(self.numpy, {"x": npy([1, 2, 3, 4], (2, 2))},
                                         {"x": npy([1, 3, 2, 4], (2, 2), fortran=True)}))

    def test_missing_extra_malformed_and_failure_results(self):
        for module, good in ((self.numpy, npy([1.])),):
            for bad in (b"bad", r.bundle({"result.npy": b"\x93NUMPY\x01\x00"}),
                        r.canonical_json({"format": "workload-failure-v1", "exit_code": 2})):
                self.assertFalse(r.compare_results(module, {"x": good}, {"x": bad}))
            self.assertFalse(r.compare_results(module, {"x": good}, {}))
            self.assertFalse(r.compare_results(module, {"x": good}, {"x": good, "extra": good}))

    def test_bundle_keeps_original_sha_and_payload_length(self):
        data = r.bundle({"result.bin": b"abc", "status.json": b'{"status":0}\n'})
        self.assertEqual(r.unbundle(data), {"result.bin": b"abc", "status.json": b'{"status":0}\n'})
        self.assertEqual(r.fingerprint(data, "named-files-v1"),
                         (16, hashlib.sha256(data).hexdigest()))

class ResidentTests(unittest.TestCase):
    SCRIPT = ("import os,signal,time,pathlib;time.sleep(.05);"
              "os.kill(os.getpid(),signal.SIGSTOP);time.sleep(.03);"
              "os.kill(os.getpid(),signal.SIGSTOP);time.sleep(.05);"
              "pathlib.Path('result').write_bytes(b'complete')")

    def execute(self, docker=False, script=None, timeout=3):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            ctx = r.Context(root, build, root, root, ROOT / "tools/runner", docker=docker)
            image = os.environ.get("RESIDENT_TEST_IMAGE", "peter-regression:terser-minify")
            executor = DockerExecutor(image) if docker else r.LocalExecutor()
            result = executor.run([r.Step(
                ["python3" if docker else sys.executable, "-c", script or self.SCRIPT],
                timeout=timeout, resident=True)], ctx, {})
            raw = (root / "result").read_bytes() if (root / "result").exists() else None
            return result[0], raw

    def test_local_resident_keeps_export_outside_operation(self):
        result, data = self.execute()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.telemetry["execution_protocol"], "complete")
        self.assertEqual(data, b"complete")
        self.assertIsNone(result.t0_ns)

    def test_missing_stop_is_candidate_status_not_harness_error(self):
        result, _ = self.execute(script="raise SystemExit(7)")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.telemetry["execution_protocol"], "missing-ready-stop")

    def test_resident_timeout_kills_stopped_or_busy_process(self):
        result, _ = self.execute(script="import time;time.sleep(30)", timeout=.05)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.telemetry["execution_protocol"], "timeout")

    def test_descendants_cannot_compute_during_untimed_export(self):
        script = (
            "import os,signal,time,pathlib;"
            "os.kill(os.getpid(),signal.SIGSTOP);"
            "pid=os.fork();"
            "\nif pid==0:\n"
            " time.sleep(.08);pathlib.Path('result').write_bytes(b'escaped');os._exit(0)\n"
            "os.kill(os.getpid(),signal.SIGSTOP);time.sleep(.15)\n")
        result, data = self.execute(script=script)
        self.assertIsNone(data)
        self.assertTrue(result.leftovers)
        self.assertEqual(result.telemetry["execution_protocol"], "complete")

    @unittest.skipUnless(shutil.which("docker") and subprocess.run(
        ["docker", "image", "inspect", os.environ.get("RESIDENT_TEST_IMAGE", "peter-regression:terser-minify")],
        capture_output=True).returncode == 0, "Requires built public image")
    def test_docker_timing_excludes_preparation_and_export(self):
        result, data = self.execute(docker=True)
        self.assertEqual(data, b"complete")
        self.assertEqual(result.telemetry["execution_protocol"], "complete")
        self.assertGreater(result.t1_ns - result.t0_ns, 20_000_000)
        self.assertLess(result.t1_ns - result.t0_ns, 90_000_000)


if __name__ == "__main__":
    unittest.main()
