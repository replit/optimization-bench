"""Shared protocol/executor tests; image baseline gates establish library parity."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
import runner as r
from docker_executor import DockerExecutor

DOCKER_IMAGE = "python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144"
DOCKER = (shutil.which("docker") and
          subprocess.run(["docker", "image", "inspect", DOCKER_IMAGE], capture_output=True).returncode == 0)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.build = self.root / "build"
        self.build.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.ctx = r.Context(self.scratch, self.build, self.inputs, self.root / "projects", self.root)

    def manifest(self, ids=("first", "second")):
        (self.inputs / "files").mkdir(exist_ok=True)
        docs = []
        for i, name in enumerate(ids):
            data = f"original {i}".encode()
            digest = hashlib.sha256(data).hexdigest()
            (self.inputs / "files" / digest).write_bytes(data)
            docs.append({"path": name, "sha256": digest, "bytes": len(data),
                         "expect_len": 123, "expect_sha": "protected"})
        manifest = {"splits": {"train": [{"docs": docs}]}}
        (self.inputs / "manifest.json").write_text(json.dumps(manifest))
        return docs

    def test_canonical_json_and_snapshot_are_stable(self):
        self.assertEqual(r.canonical_json({"z": "é", "a": 1}), b'{"a":1,"z":"\\u00e9"}')
        self.assertEqual(r.canonical_json({"z": "é"}, False), '{"z":"é"}'.encode())
        (self.scratch / "b").write_bytes(b"\x00\xff")
        (self.scratch / "a").symlink_to("b")
        self.assertEqual(r.snapshot(self.scratch), ({"b": "AP8="}, {"a": "b"}))

    def test_full_project_inventory_detects_extra_files_and_symlinks(self):
        project = self.ctx.projects / "x"
        project.mkdir(parents=True)
        (project / "q").write_bytes(b"query")
        (project / "link").symlink_to("q")
        digest = r.inventory_sha256(project)
        self.ctx.copy_project("x", digest)
        (project / "extra").write_text("changed")
        with self.assertRaises(r.HarnessError):
            self.ctx.copy_project("x", digest, "other")
        with self.assertRaises(r.HarnessError):
            self.ctx.copy_project("../outside", digest)

    def test_content_addresses_lengths_duplicates_and_escape(self):
        for mode in ("checksum", "length", "duplicate", "escape", "symlink"):
            with self.subTest(mode=mode):
                docs = self.manifest()
                path = self.inputs / "files" / docs[0]["sha256"]
                if mode == "checksum":
                    path.write_bytes(b"changed")
                elif mode == "length":
                    docs[0]["bytes"] += 1
                elif mode == "duplicate":
                    docs[1]["path"] = docs[0]["path"]
                elif mode == "escape":
                    docs[0]["sha256"] = "../escape"
                else:
                    outside = self.root / "outside"
                    outside.write_bytes(path.read_bytes())
                    path.unlink()
                    path.symlink_to(outside)
                (self.inputs / "manifest.json").write_text(json.dumps({"splits": {"train": [{"docs": docs}]}}))
                with self.assertRaises(r.HarnessError):
                    r.documents(self.inputs)
                if path.is_symlink():
                    path.unlink()

    def test_ruff_inventory_excludes_only_vcs_metadata_not_source_or_gitignore(self):
        project = self.ctx.projects / "x"
        (project / ".git").mkdir(parents=True)
        (project / ".git/fetch-log").write_text("variable fetch metadata")
        (project / ".gitignore").write_text("ignored/\n")
        (project / "source.py").write_text("original\n")
        digest = r.inventory_sha256(project, exclude_vcs=True)
        (project / ".git/fetch-log").write_text("a later fetch")
        self.ctx.copy_project("x", digest, exclude_vcs=True)
        self.assertEqual((self.scratch / "project/.git/fetch-log").read_text(), "a later fetch")
        (project / ".gitignore").write_text("changed/\n")
        with self.assertRaises(r.HarnessError):
            self.ctx.copy_project("x", digest, "other", exclude_vcs=True)

    def test_batch_seed_shuffles_without_exposing_expectations(self):
        docs = self.manifest(tuple(f"case-{i}" for i in range(12)))
        self.ctx.seed = 31
        self.ctx.batch(r.documents(self.inputs))
        rows = json.loads((self.scratch / "cases.json").read_text())
        self.assertNotEqual([d["path"] for d in rows], [d["path"] for d in docs])
        self.assertEqual({d["path"] for d in docs}, {d["path"] for d in rows})
        self.assertTrue(all("expect_len" not in d and "expect_sha" not in d for d in rows))

    def test_batch_candidate_failures_and_streams_are_results_not_harness_errors(self):
        docs = self.manifest()
        for doc in docs:
            (self.scratch / (doc["path"] + ".out")).write_bytes(b"valid")
        baseline = self.ctx.batch_results(docs, [r.StepResult(0, b"", b"")])
        for step in (r.StepResult(9, b"", b""), r.StepResult(None, b"partial", b""),
                     r.StepResult(0, b"unexpected", b""), r.StepResult(0, b"", b"warning")):
            self.assertNotEqual(self.ctx.batch_results(docs, [step]), baseline)
        (self.scratch / (docs[0]["path"] + ".out")).unlink()
        self.assertNotEqual(self.ctx.batch_results(docs, [r.StepResult(0, b"", b"")]), baseline)

    def test_missing_build_is_invalid_run(self):
        self.ctx.build = self.root / "missing"
        with self.assertRaises(r.HarnessError):
            r.run_case(types.SimpleNamespace(), r.Case("x", {}), self.ctx, r.LocalExecutor())

    def test_invalid_commands_are_harness_errors(self):
        for args in ([], [42], ["bad\0arg"]):
            with self.assertRaises(r.HarnessError):
                r.Step(args)

class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.build.mkdir()

    def execute(self, scripts, docker=False, timeout=5):
        scratch = self.root / ("docker" if docker else "local")
        scratch.mkdir()
        ctx = r.Context(scratch, self.build, self.root, self.root, self.root, docker=docker)
        executor = DockerExecutor(DOCKER_IMAGE) if docker else r.LocalExecutor()
        python = "/usr/local/bin/python" if docker else sys.executable
        result = executor.run([r.Step([python, "-c", s], timeout=timeout) for s in scripts], ctx, {})
        return result, executor

    def test_local_detached_helper_stopped_killed_reaped_and_leftovers_recorded(self):
        self.detached(False)

    @unittest.skipUnless(DOCKER, "Docker with pinned Python image required")
    def test_docker_detached_helper_stopped_killed_and_leftovers_recorded(self):
        self.detached(True)

    def detached(self, docker):
        script = ("import subprocess,sys,time,pathlib\n"
                  "p=subprocess.Popen([sys.executable,'-c',"
                  "'import pathlib,time;pathlib.Path(\"ready\").touch();time.sleep(30)',"
                  "'detached-runner-test'],start_new_session=True)\n"
                  "while not pathlib.Path('ready').exists():time.sleep(.01)\n"
                  "print('main exit',flush=True)\n")
        next_step = ("import os,pathlib\n"
                     "for p in pathlib.Path('/proc').iterdir():\n"
                     " if p.name.isdigit() and int(p.name)!=os.getpid():\n"
                     "  try:data=(p/'cmdline').read_bytes()\n"
                     "  except OSError:continue\n"
                     "  assert b'detached-runner-test' not in data\n"
                     "print('next step')")
        results, _ = self.execute([script, next_step], docker)
        self.assertEqual(results[0].stdout, b"main exit\n")
        self.assertGreaterEqual(len(results[0].leftovers), 1)
        self.assertTrue(any("detached-runner-test" in p["cmdline"] for p in results[0].leftovers))
        self.assertEqual(results[1].stdout, b"next step\n")
        self.assertEqual(results[1].exit_code, 0, results[1].stderr)
        if not docker:
            for child in results[0].leftovers:
                self.assertFalse(Path(f"/proc/{child['pid']}").exists())
        else:
            for child in results[0].leftovers:
                self.assertEqual(child["threads"], len(child["thread_identities"]))
                self.assertEqual(child["step_index"], 0)
                self.assertEqual(child["observation_boundary"], "process-exit")
                self.assertTrue(all("start_ticks" in row and "tid" in row for row in child["thread_identities"]))

    def test_local_timeout_is_null_status_with_complete_streams(self):
        results, _ = self.execute(["import time;print('partial',flush=True);time.sleep(30)"], timeout=.2)
        self.assertIsNone(results[0].exit_code)
        self.assertEqual(results[0].stdout, b"partial\n")
        self.assertTrue(results[0].leftovers)
        self.assertIsNone(results[0].t0_ns)

    @unittest.skipUnless(DOCKER, "Docker with pinned Python image required")
    def test_docker_timeout_readonly_build_and_sum_of_step_times(self):
        (self.build / "protected").write_text("original")
        script = ("from pathlib import Path\n"
                  "try:Path('/app/build/protected').write_text('bad')\n"
                  "except OSError:print('readonly',flush=True)\n"
                  "import time;time.sleep(30)")
        results, executor = self.execute([script, "print('ok')"], True, timeout=.3)
        self.assertIsNone(results[0].exit_code)
        self.assertEqual(results[0].stdout, b"readonly\n")
        self.assertEqual((self.build / "protected").read_text(), "original")
        self.assertEqual(executor.case_time_ns, sum(s.t1_ns - s.t0_ns for s in results))
        self.assertGreater(executor.case_time_ns, 0)

    def test_fixed_environment_and_fresh_home_tmp_per_step(self):
        with patch.dict(os.environ, {"RUNNER_CALLER_SECRET": "must-not-inherit"}):
            results, _ = self.execute([
                "import os;print(os.environ.get('RUNNER_CALLER_SECRET','absent'));print(os.environ['HOME']);print(os.environ['TMPDIR'])",
                "import os;print(os.environ['HOME']);print(os.environ['TMPDIR'])"])
        self.assertTrue(results[0].stdout.startswith(b"absent\n"))
        self.assertNotEqual(results[0].stdout.splitlines()[-2:], results[1].stdout.splitlines())

    def test_nonzero_exit_and_complete_streams_are_candidate_results_locally(self):
        results, _ = self.execute(["import sys;sys.stdout.buffer.write(b'out\\xff');sys.stderr.buffer.write(b'err');sys.exit(7)"])
        self.assertEqual((results[0].exit_code, results[0].stdout, results[0].stderr), (7, b"out\xff", b"err"))

    @unittest.skipUnless(DOCKER, "Docker with pinned Python image required")
    def test_nonzero_exit_and_complete_streams_are_candidate_results_in_docker(self):
        results, _ = self.execute(["import sys;sys.stdout.buffer.write(b'out\\xff');sys.stderr.buffer.write(b'err');sys.exit(7)"], True)
        self.assertEqual((results[0].exit_code, results[0].stdout, results[0].stderr), (7, b"out\xff", b"err"))


if __name__ == "__main__":
    unittest.main()
