"""NumPy local receipts stay compatible without retaining complete suite outputs."""
import contextlib
import gc
import hashlib
import importlib.util
import io
from pathlib import Path
import random
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import weakref


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tasks/numpy-quantile/environment/workload/runner.py"
SPEC = importlib.util.spec_from_file_location("numpy_output_runner", PATH)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def original_fingerprint(data):
    try:
        size = sum(map(len, runner.unbundle(data).values()))
    except (ValueError, UnicodeError):
        size = len(data)
    return size, hashlib.sha256(data).hexdigest()


class OutputMemoryTests(unittest.TestCase):
    def test_fingerprints_match_original_including_invalid_envelopes(self):
        samples = [b"", b"failure", b"a\0-1\0", b"a\0" + b"10\0short",
                   b"a\0  +2 \0xy", b"a\0x\0", b"\xff\0" + b"0\0",
                   b"a/b\0" + b"0\0", b"a\0" + b"0\0a\0" + b"0\0",
                   runner.bundle({"a": b"\0hello", "z": b""})]
        rng = random.Random(14)
        samples += [bytes(rng.choice([0, 47, 48, 49, 65, 255])
                          for _ in range(rng.randrange(80))) for _ in range(500)]
        for data in samples:
            with self.subTest(data=data):
                self.assertEqual(runner.fingerprint(data, "named-files-v1"),
                                 original_fingerprint(data))
                self.assertEqual(runner.fingerprint(data),
                                 (len(data), hashlib.sha256(data).hexdigest()))

    def test_fingerprinting_does_not_copy_large_payload(self):
        data = runner.bundle({"large": b"x" * (4 * 1024 * 1024)})
        tracemalloc.start()
        try:
            result = runner.fingerprint(data, "named-files-v1")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result, original_fingerprint(data))
        self.assertLess(peak, 256 * 1024)

    def test_summary_rejects_duplicate_and_invalid_names(self):
        summaries = {}
        runner.summarize_outputs({"ok": b"x"}, summaries, None, None)
        for name in ("ok", "../bad", ""):
            with self.subTest(name=name), self.assertRaises(runner.HarnessError):
                runner.summarize_outputs({name: b"y"}, summaries, None, None)

    def test_main_releases_previous_case_and_preserves_sorted_output_files(self):
        class Buffer(bytearray):
            pass

        for export in (False, True):
            with self.subTest(export=export), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task = root / "task.py"
                task.write_text(
                    "from types import SimpleNamespace\n"
                    "def cases(inputs):\n"
                    "    return [SimpleNamespace(id=n) for n in ['z', 'a']]\n")
                previous = []

                def execute(module, case, ctx, executor):
                    gc.collect()
                    self.assertTrue(all(ref() is None for ref in previous))
                    data = Buffer(case.id.encode() * 100)
                    previous.append(weakref.ref(data))
                    return {case.id: data}, []

                argv = ["runner", "--task", str(task), "--build", str(root)]
                if export:
                    argv += ["--outputs", str(root / "outputs")]
                stdout = io.StringIO()
                with patch.object(sys, "argv", argv), \
                        patch.object(runner, "run_case", execute), \
                        contextlib.redirect_stdout(stdout):
                    self.assertEqual(runner.main(), 0)
                expected = "".join(
                    f"{name}\t100\t{hashlib.sha256(name.encode() * 100).hexdigest()}\n"
                    for name in ("a", "z"))
                self.assertEqual(stdout.getvalue(), expected)
                if export:
                    for name in ("a", "z"):
                        self.assertEqual((root / "outputs" / (name + ".out")).read_bytes(),
                                         name.encode() * 100)
                        self.assertEqual((root / "outputs" / name / "steps.json").read_text(),
                                         "[]")


if __name__ == "__main__":
    unittest.main()
