import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Context, HarnessError, LocalExecutor, Step


class CliStreamTests(unittest.TestCase):
    def test_image_staged_public_inputs_map_to_the_protected_image_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctx = Context(root / "scratch", root / "build", root / "image-workload/inputs",
                          root / "projects", root / "repository-task/workload", docker=True)
            self.assertEqual(ctx.target(ctx.inputs / "files/pinned-input"),
                             "/app/workload/inputs/files/pinned-input")

    def test_local_stdin_and_home_are_prepared_without_a_wrapper_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "case").mkdir()
            (root / "stdin").write_bytes(b"exact input")
            context = Context(root, root, root, root, root)
            step = Step([sys.executable, "-c",
                         "import os,sys;sys.stdout.buffer.write(sys.stdin.buffer.read());"
                         "assert os.environ['HOME']==os.getcwd()"],
                        cwd="case", stdin_file="stdin", home_cwd=True)
            result, = LocalExecutor().run([step], context, {})
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout, b"exact input")
            self.assertEqual(result.stderr, b"")
            self.assertIsNone(result.t0_ns)

    def test_stdin_cannot_escape_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = Context(root, root, root, root, root)
            with self.assertRaises(HarnessError):
                LocalExecutor().run([Step(["true"], stdin_file="../secret")], context, {})
