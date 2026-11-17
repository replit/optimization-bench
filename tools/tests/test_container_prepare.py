"""Protected preparation is shared, untimed, once per fresh container."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
from docker_executor import DockerExecutor
from preparation import metadata, validate_receipt
from runner import Context, HarnessError, Step
from judge import differential, policy

IMAGE = os.environ.get("RESIDENT_TEST_IMAGE")
HOOK = "/app/workload/guards/prepare.py"
SCRIPT = b"""#!/usr/bin/env python3
import argparse,json,sys,time
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--steps');p.add_argument('--inputs');p.add_argument('--fail',action='store_true')
a=p.parse_args()
request=Path(a.steps)
assert json.loads(request.read_text())['steps']
try:request.write_text('bad')
except OSError:pass
else:raise RuntimeError('trusted steps are writable')
assert not Path('/scratch/steps.json').exists()
inputs=Path(a.inputs)
if (inputs/'input').exists():assert (inputs/'input').read_bytes()==b'protected inputs'
else:assert (inputs/'case.json').is_file()
count=Path('/scratch/prepare-count')
assert not count.exists()
count.write_text('1')
print('prepared once; trusted steps readonly',flush=True)
print('preparation stderr',file=sys.stderr,flush=True)
time.sleep(.15)
sys.exit(7 if a.fail else 0)
"""


def fixture(root):
    task = Path(root) / "task"
    script = task / "environment/workload/guards/prepare.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(SCRIPT)
    script.chmod(0o755)
    (task / "tests").mkdir()
    (task / "tests/judge.json").write_text(json.dumps({"source_path": "/app/source"}))
    argv = [HOOK, "--steps", "{steps}", "--inputs", "{inputs}"]
    (task / "task.json").write_text(json.dumps({"container_prepare": argv}))
    return task


class PreparationMetadataTests(unittest.TestCase):
    def test_receipt_validation_rejects_tampered_provenance(self):
        expected = {"argv": [HOOK], "executable_sha256": "a" * 64}
        receipt = {**expected, "exit_code": 0, "timed": False, "duration_ns": 1,
                   "leftovers": [], "stdout_base64": "", "stderr_base64": ""}
        validate_receipt(receipt, expected)
        for change in ({"exit_code": 7}, {"exit_code": False}, {"timed": True},
                       {"executable_sha256": "b" * 64}, {"argv": ["/app/build/evil"]},
                       {"duration_ns": -1}, {"duration_ns": "1"}, {"leftovers": [{"pid": 1}]},
                       {"stdout_base64": "not base64!"}, {"harness_error": "forged"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_receipt({**receipt, **change}, expected)

    def test_absent_field_is_optional_and_invalid_or_editable_hooks_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = fixture(temporary)
            definition = task / "task.json"
            original = definition.read_text()
            self.assertEqual(metadata(task)["argv"][0], HOOK)
            definition.write_text("{}")
            self.assertIsNone(metadata(task))
            self.assertIsNone(metadata(None))
            for argv in ([], "shell command", ["/app/build/evil"], ["/app/source/evil"],
                         ["/app/workload/inputs/evil"], ["/app/workload/../build/evil"]):
                definition.write_text(json.dumps({"container_prepare": argv}))
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    metadata(task)
            definition.write_text(original)
            (task / "tests/judge.json").write_text(json.dumps({"source_path": "/app/workload"}))
            with self.assertRaisesRegex(ValueError, "overlaps candidate"):
                metadata(task)

    def test_existing_source_policy_rejects_candidate_writes_to_task_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pristine, candidate = root / "pristine", root / "candidate"
            pristine.mkdir()
            (pristine / "source.py").write_text("value = 1\n")
            shutil.copytree(pristine, candidate)
            script = candidate / "environment/workload/guards/prepare.py"
            script.parent.mkdir(parents=True)
            script.write_bytes(SCRIPT)
            rules = {"source_roots": ["."], "allowed_files": ["source.py"],
                     "allowed_extensions": [".py"], "protected_paths": [],
                     "max_source_files": 5, "max_source_bytes": 10000}
            with self.assertRaises(ValueError):
                policy.envelope(candidate, pristine, rules)


@unittest.skipUnless(IMAGE, "RESIDENT_TEST_IMAGE required for real container tests")
class PreparationDockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.task = fixture(cls.root)
        (cls.root / "Dockerfile").write_text(
            f"FROM {IMAGE}\nCOPY task/environment/workload/guards/ /app/workload/guards/\n")
        cls.image = "container-prepare-test:" + str(os.getpid())
        result = subprocess.run(["docker", "build", "--network=none", "-t", cls.image, str(cls.root)],
                                capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "image", "rm", cls.image], capture_output=True)
        cls.temporary.cleanup()

    def setUp(self):
        self.definition = self.task / "task.json"
        self.definition.write_text(json.dumps({"container_prepare":
            [HOOK, "--steps", "{steps}", "--inputs", "{inputs}"]}))
        (self.task / "tests/judge.json").write_text(json.dumps({"source_path": "/app/source"}))
        script = self.task / "environment/workload/guards/prepare.py"
        script.write_bytes(SCRIPT)
        script.chmod(0o755)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        scratch, build, inputs = root / "scratch", root / "build", root / "inputs"
        for path in (scratch, build, inputs):
            path.mkdir()
        (inputs / "input").write_bytes(b"protected inputs")
        self.ctx = Context(scratch, build, inputs, root, self.task / "environment/workload", docker=True)

    def steps(self):
        return [Step(["python3", "-c",
            "from pathlib import Path;assert Path('/scratch/prepare-count').read_text()=='1';print('timed')"])
                for _ in range(2)]

    def test_default_and_explicit_task_paths_share_once_only_untimed_preparation(self):
        (self.ctx.build / "json.py").write_text("raise RuntimeError('candidate hijacked preparation')\n")
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                if explicit:
                    self.ctx.scratch = Path(self.tmp.name) / "explicit"
                    self.ctx.scratch.mkdir()
                executor = DockerExecutor(self.image, task=self.task if explicit else None)
                results = executor.run(self.steps(), self.ctx, {"PYTHONPATH": "/app/build"})
                self.assertEqual([r.stdout for r in results], [b"timed\n", b"timed\n"])
                receipt = executor.container_prepare
                self.assertEqual(receipt["exit_code"], 0)
                self.assertFalse(receipt["timed"])
                self.assertGreater(receipt["duration_ns"], 150_000_000)
                self.assertEqual(base64.b64decode(receipt["stdout_base64"]),
                                 b"prepared once; trusted steps readonly\n")
                self.assertEqual(base64.b64decode(receipt["stderr_base64"]), b"preparation stderr\n")
                self.assertEqual(executor.case_time_ns, sum(r.t1_ns-r.t0_ns for r in results))
                self.assertEqual(results[0].telemetry["container_prepare"], receipt)
                self.assertNotIn("container_prepare", results[1].telemetry)
                self.assertEqual((self.ctx.scratch / "prepare-count").read_text(), "1")

    def test_failure_is_infrastructure_failure_and_never_reaches_timed_steps(self):
        definition = json.loads(self.definition.read_text())
        definition["container_prepare"].append("--fail")
        self.definition.write_text(json.dumps(definition))
        executor = DockerExecutor(self.image)
        with self.assertRaisesRegex(HarnessError, "Container preparation failed"):
            executor.run(self.steps(), self.ctx, {})
        self.assertEqual(executor.container_prepare["exit_code"], 7)
        self.assertIn("stderr_base64", executor.container_prepare)
        self.assertFalse((self.ctx.scratch / "step-0.json").exists())

    def test_image_hook_must_match_protected_task_bytes(self):
        script = self.task / "environment/workload/guards/prepare.py"
        script.write_bytes(SCRIPT + b"\n# changed after image construction\n")
        executor = DockerExecutor(self.image)
        with self.assertRaisesRegex(HarnessError, "differs from protected"):
            executor.run(self.steps(), self.ctx, {})
        self.assertFalse((self.ctx.scratch / "prepare-count").exists())

    def test_no_field_keeps_legacy_request_and_receipts(self):
        self.definition.write_text("{}")
        executor = DockerExecutor(self.image)
        result = executor.run([Step(["python3", "-c", "print('plain')"])], self.ctx, {})
        self.assertEqual(result[0].stdout, b"plain\n")
        self.assertIsNone(executor.container_prepare)
        self.assertIsInstance(json.loads((self.ctx.scratch / "steps.json").read_text()), list)
        self.assertFalse((self.ctx.scratch / "container-prepare.json").exists())
        self.assertNotIn("container_prepare", result[0].telemetry)

    def test_identical_pristine_differential_observations_exclude_preparation_receipts(self):
        folder = self.task / "tests/differential"
        folder.mkdir(exist_ok=True)
        (folder / "run.py").write_text(
            "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))\n")
        (self.task / "tests/judge.json").write_text(json.dumps({
            "source_path": "/app/source", "differential": {
                "command": ["python3", "/verifier-differential/run.py"]}}))
        left = differential.execute(self.task, self.ctx.build, self.image, None, {"bytes": "same"}, 20)
        right = differential.execute(self.task, self.ctx.build, self.image, None, {"bytes": "same"}, 20)
        self.assertEqual(left, right)
        self.assertEqual(json.dumps(left), json.dumps(right))
        self.assertEqual(left["result"], {"bytes": "same"})
        self.assertNotIn("container_prepare", left)
        self.assertNotIn("receipts", left)
        # Even a distinct duration cannot affect the strict wrapper equality.
        right.receipts["container_prepare"]["duration_ns"] += 1
        self.assertEqual(left, right)
        self.assertEqual(left.receipts["container_prepare"]["exit_code"], 0)

    def test_candidate_receipt_edit_is_an_infrastructure_failure(self):
        executor = DockerExecutor(self.image)
        step = Step(["python3", "-c",
            "from pathlib import Path;Path('/scratch/container-prepare.json').write_text('{}');print('changed')"])
        with self.assertRaisesRegex(HarnessError, "receipt changed during execution"):
            executor.run([step], self.ctx, {})
        self.assertIn("receipt changed", executor.container_prepare["harness_error"])
