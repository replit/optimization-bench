"""Real OS/stdlib controls in pinned containers; not task qualification."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools.fuzz_runners import cli_faults as faults

sys.path.insert(0, str(faults.ROOT / "tools/runner"))
from runner import Context, Step
from docker_executor import DockerExecutor

PYTHON_IMAGE = "python@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3"
GO_IMAGE = "golang@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c"


def image(ref):
    result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref],
                            capture_output=True, text=True)
    if result.returncode:
        raise unittest.SkipTest("pinned Docker image not cached: " + ref)
    return result.stdout.strip()


class RealCliFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker unavailable")
        info = subprocess.run(["docker", "info"], capture_output=True)
        if info.returncode:
            raise unittest.SkipTest("Docker daemon unavailable")
        cls.python_image, cls.go_image = image(PYTHON_IMAGE), image(GO_IMAGE)
        if os.environ.get("OPTIMBENCH_CLI_FAULT_EVIDENCE"):
            cls.temporary = None
            cls.root = Path(os.environ["OPTIMBENCH_CLI_FAULT_EVIDENCE"])
            cls.root.mkdir(parents=True, exist_ok=False)
        else:
            cls.temporary = tempfile.TemporaryDirectory(prefix="cli-fault-controls-")
            cls.root = Path(cls.temporary.name)
        cls.empty = cls.root / "empty-build"
        cls.empty.mkdir()
        cls.small, cls.large = cls.root / "small", cls.root / "large"
        for directory, data in ((cls.small, b"select 1;\n"), (cls.large, b"select 123456789;\n" * 131072)):
            directory.mkdir()
            (directory / "input.sql").write_bytes(data)
            (directory / "out").mkdir()
        cls.cat = {}
        for label, inputs in (("small", cls.small), ("large", cls.large)):
            cls.cat[label] = faults.run_pair(
                step=Step(["/bin/cat", "input.sql"], timeout=15), inputs=inputs,
                baseline=cls.empty, candidate=cls.empty, image=cls.python_image,
                out=cls.root / ("cat-" + label), watch_inputs=["input.sql"],
                faults=("healthy", "dev_full", "closed_reader", "closed_stdout"),
                case_id="cat-" + label, payload_class=label)
        cls.copy = faults.run_pair(
            step=Step(["/bin/cp", "input.sql", "out/result.sql"], timeout=15), inputs=cls.small,
            baseline=cls.empty, candidate=cls.empty, image=cls.python_image, out=cls.root / "copy",
            watch_inputs=["input.sql"], watch_outputs=["out"], output_dirs=["out"],
            required_outputs=["out/result.sql"], faults=("healthy", "readonly_dir"),
            case_id="copy-small", payload_class="small")
        cls.binaries = {}
        source = faults.HERE / "fixtures/cli_buffered.go"
        scratch = cls.root / "compile"
        scratch.mkdir()
        modes = ("correct", "ignore_flush", "rewrite_on_error", "success_on_error")
        source_build = cls.root / "source-build"
        source_build.mkdir()
        shutil.copy(source, source_build / "main.go")
        ctx = Context(scratch, source_build, source_build, source_build, source_build, docker=True)
        compiled = DockerExecutor(cls.go_image).run([
            Step(["/usr/local/go/bin/go", "build", "-ldflags=-X main.behavior=" + mode,
                  "-o", "/scratch/cli-" + mode, "/app/build/main.go"], timeout=180)
            for mode in modes], ctx,
            {"GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOCACHE": "/scratch/cache"})
        for mode, checked in zip(modes, compiled):
            if checked.exit_code != 0:
                raise RuntimeError("pinned Go CLI control build failed: " + checked.stderr.decode(errors="replace")[-2000:])
            build = cls.root / ("build-" + mode)
            build.mkdir()
            shutil.copy(source, build / "main.go")
            shutil.copy(scratch / ("cli-" + mode), build / "cli")
            cls.binaries[mode] = build
        cls.buffered = {}
        for label, inputs in (("small", cls.small), ("large", cls.large)):
            cls.buffered[label] = faults.run_pair(
                step=Step(["{build}/cli", "input.sql"], timeout=15), inputs=inputs,
                baseline=cls.binaries["correct"], candidate=cls.binaries["ignore_flush"],
                image=cls.go_image, out=cls.root / ("buffered-" + label),
                watch_inputs=["input.sql"], faults=("healthy", "dev_full"),
                case_id="buffered-" + label, payload_class=label)
        cls.negatives = {}
        for mode in ("rewrite_on_error", "success_on_error"):
            cls.negatives[mode] = faults.run_pair(
                step=Step(["{build}/cli", "input.sql"], timeout=15), inputs=cls.small,
                baseline=cls.binaries["correct"], candidate=cls.binaries[mode], image=cls.go_image,
                out=cls.root / ("negative-" + mode), watch_inputs=["input.sql"],
                faults=("dev_full",), case_id=mode, payload_class="small")

    @classmethod
    def tearDownClass(cls):
        if cls.temporary is None:
            return
        # Container-created readonly dirs need restoration before non-root
        # test environments can remove their temporary case trees.
        for path in cls.root.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                try:
                    path.chmod(0o777)
                except PermissionError:
                    pass
        cls.temporary.cleanup()

    def test_pristine_coreutils_fault_matrix_has_real_errno_and_signal_facts(self):
        for rows in self.cat.values():
            for pair in rows["observations"].values():
                self.assertTrue(all(row["status"] == "observed" for row in pair.values()), pair)
            observations = rows["observations"]
            self.assertEqual(observations["healthy"]["baseline"]["exit_code"], 0)
            self.assertIn("No space left", observations["dev_full"]["baseline"]["stderr"]["headline"])
            self.assertEqual(observations["closed_reader"]["baseline"]["signal"], 13)
            self.assertEqual(observations["closed_reader"]["baseline"]["stderr"]["bytes"], 0)
            self.assertIn("Bad file descriptor", observations["closed_stdout"]["baseline"]["stderr"]["headline"])

    def test_readonly_output_failure_observed_outside_stdout(self):
        row = self.copy["observations"]["readonly_dir"]["baseline"]
        self.assertEqual(row["status"], "observed")
        self.assertNotEqual(row["exit_code"], 0)
        self.assertIn("Read-only file system", row["stderr"]["headline"])
        self.assertTrue(row["setup_readonly_mounts"][0]["readonly_filesystem_verified"])
        self.assertEqual(row["input_before"], row["input_after"])
        self.assertEqual(row["required_outputs"]["out/result.sql"]["out/result.sql"]["kind"], "missing")
        healthy = self.copy["observations"]["healthy"]["baseline"]
        self.assertEqual(healthy["exit_code"], 0)
        self.assertEqual(healthy["required_outputs"]["out/result.sql"]["out/result.sql"]["kind"], "file")

    def test_small_flush_only_control_and_large_write_control_both_needed(self):
        small = self.buffered["small"]["observations"]["dev_full"]
        large = self.buffered["large"]["observations"]["dev_full"]
        self.assertNotEqual(small["baseline"]["exit_code"], 0)
        self.assertEqual(small["candidate"]["exit_code"], 0)  # Known ignored-Flush control.
        self.assertNotEqual(large["baseline"]["exit_code"], 0)
        self.assertNotEqual(large["candidate"]["exit_code"], 0)  # Write fails before Flush.
        healthy = self.buffered["large"]["observations"]["healthy"]["baseline"]
        self.assertGreater(healthy["stdout"]["bytes"], 1024 * 1024)

    def test_rewrite_and_swallowed_error_controls_remain_visible(self):
        pair = self.negatives["rewrite_on_error"]["observations"]["dev_full"]
        self.assertEqual(pair["baseline"]["input_before"], pair["baseline"]["input_after"])
        self.assertNotEqual(pair["candidate"]["input_before"], pair["candidate"]["input_after"])
        pair = self.negatives["success_on_error"]["observations"]["dev_full"]
        self.assertNotEqual(pair["baseline"]["exit_code"], 0)
        self.assertEqual(pair["candidate"]["exit_code"], 0)

    def test_all_observers_nonroot_and_no_private_verdict(self):
        results = [*self.cat.values(), self.copy, *self.buffered.values(), *self.negatives.values()]
        for result in results:
            for pair in result["observations"].values():
                for row in pair.values():
                    self.assertEqual(row["status"], "observed", row)
                    self.assertEqual(row["uid"], 10000)
                    self.assertIsNone(row["candidate_verdict"])
                    self.assertFalse(row["timed_out"])
                    self.assertFalse(row["stream_budget_exceeded"])
                    self.assertEqual(row["shared_shim_leftovers"], [])

    def test_missing_executable_is_infrastructure_not_candidate_exit(self):
        value = faults.run_pair(
            step=Step(["/does-not-exist"], timeout=5), inputs=self.small,
            baseline=self.empty, candidate=self.empty, image=self.python_image,
            out=self.root / "missing-program", watch_inputs=["input.sql"], faults=("healthy",))
        for row in value["observations"]["healthy"].values():
            self.assertEqual(row["status"], "invalid_run")
            self.assertIsNone(row["candidate_verdict"])
            self.assertNotIn("exit_code", row)

    def test_descendants_stopped_before_final_input_snapshot(self):
        build = self.root / "background-build"
        build.mkdir()
        (build / "background.py").write_text(
            "import os,time,pathlib\n"
            "if os.fork()==0:\n"
            " os.setsid();time.sleep(2);pathlib.Path('input.sql').write_text('late mutation');os._exit(0)\n"
            "os._exit(0)\n")
        value = faults.run_pair(
            step=Step(["/usr/local/bin/python", "{build}/background.py"], timeout=5),
            inputs=self.small, baseline=build, candidate=build, image=self.python_image,
            out=self.root / "background", watch_inputs=["input.sql"], faults=("healthy",))
        for row in value["observations"]["healthy"].values():
            self.assertEqual(row["status"], "observed")
            self.assertTrue(row["stopped_descendants"])
            self.assertEqual(row["input_before"], row["input_after"])

    def test_stream_artifacts_retain_full_tail_after_worker_stream_cleanup(self):
        tail = b"input.sql:17:9 TAIL-PRESERVED\n"
        payload = b"Error headline\n" + b"  at worker (/source/file.js:4:2)\n" * 3000 + tail
        code = ("import pathlib,sys; data=pathlib.Path('input.sql').read_bytes(); assert data;"
                "sys.stdout.buffer.write(data);sys.stderr.buffer.write(" + repr(payload) + ")")
        value = faults.run_pair(
            step=Step(["/usr/local/bin/python", "-c", code], timeout=10), inputs=self.small,
            baseline=self.empty, candidate=self.empty, image=self.python_image,
            out=self.root / "long-stderr", watch_inputs=["input.sql"], faults=("healthy",))
        for arm, row in value["observations"]["healthy"].items():
            self.assertEqual(row["status"], "observed", row)
            self.assertTrue(row["stderr"]["head_truncated"])
            self.assertTrue(row["raw_streams_retained"])
            artifact = row["stderr"]["artifact"]
            source = self.root / "long-stderr/healthy" / arm / "stderr.bin"
            source.unlink()
            self.assertEqual(Path(artifact["path"]).read_bytes(), payload)
            self.assertEqual(artifact["sha256"], row["stderr"]["sha256"])
            self.assertTrue(artifact["complete"])
            self.assertTrue(Path(artifact["path"]).is_relative_to(self.root / "long-stderr"))

    def test_observer_and_child_actual_affinity_under_one_two_four_eight_cpus(self):
        import hashlib
        available = sorted(os.sched_getaffinity(0))
        if len(available) < 8:
            self.skipTest("actual 1/2/4/8 CPU validation requires eight available CPUs")
        builds = {}
        for arm in ("baseline", "candidate"):
            builds[arm] = self.root / ("affinity-build-" + arm)
            builds[arm].mkdir()
            (builds[arm] / "mode").write_text(arm)
        code = (
            "import json,os,pathlib; assert pathlib.Path('input.sql').read_bytes();"
            "cpus=sorted(os.sched_getaffinity(0));"
            "mode=pathlib.Path('/app/build/mode').read_text();"
            "print(json.dumps({'child_affinity':cpus,"
            "'control_value':int(not(mode=='candidate' and len(cpus)==1))}))"
        )
        for count in (1, 2, 4, 8):
            expected = available[-count:]  # Nonzero real CPU IDs, not logical counts.
            out = self.root / ("affinity-" + str(count))
            value = faults.run_pair(
                step=Step(["/usr/local/bin/python", "-c", code], timeout=10),
                inputs=self.small, baseline=builds["baseline"], candidate=builds["candidate"],
                image=self.python_image, out=out, watch_inputs=["input.sql"],
                faults=("healthy",), cpuset=",".join(map(str, expected)),
                case_id="affinity-" + str(count))
            for arm, row in value["observations"]["healthy"].items():
                self.assertEqual(row["status"], "observed", row)
                self.assertEqual(row["execution_affinity"], expected)
                observer = (out / "healthy" / arm / "observer.json").read_bytes()
                self.assertEqual(hashlib.sha256(observer).hexdigest(), row["observer_sha256"])
                self.assertEqual(json.loads(observer)["execution_affinity"], expected)
                child = json.loads(Path(row["stdout"]["artifact"]["path"]).read_bytes())
                self.assertEqual(child["child_affinity"], expected)
                # Raw negative-control observation only: central policy owns rejection.
                self.assertEqual(child["control_value"], int(not (arm == "candidate" and count == 1)))


if __name__ == "__main__":
    unittest.main()
