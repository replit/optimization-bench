"""Synthetic preparation tests only; no actual corpus or benchmark execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HELPER = (Path(__file__).resolve().parents[2] /
          "environment/workload/guards/warm_git_objects.py")
spec = importlib.util.spec_from_file_location("warm_git_objects", HELPER)
warm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warm)


class WarmGitObjectsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.inputs = self.root / "inputs"
        self.repository = self.inputs / "histories/example"
        self.pack = self.repository / ".git/objects/pack"
        self.pack.mkdir(parents=True)
        (self.pack / "pack-fixture.pack").write_bytes(b"synthetic pack bytes")
        (self.pack / "pack-fixture.idx").write_bytes(b"synthetic index bytes")
        self.step = {"argv": ["/app/build/project/git", "-C", str(self.repository),
                              "diff-tree", "--stdin", "-p"]}

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_only_pack_and_index_bytes_and_deduplicates_arms(self):
        (self.pack / "ignore.bitmap").write_bytes(b"not selected")
        result = warm.prepare([self.step, self.step], self.inputs)
        self.assertEqual(result["selected_repositories"], 1)
        self.assertEqual(result["files_read"], 2)
        self.assertEqual(result["bytes_read"], 41)
        self.assertEqual(result["buffer_bytes"], 8 * 1024 * 1024)
        self.assertEqual(result["status"], "prepared")

    def test_accepts_object_request_and_does_not_emit_repository_names(self):
        result = warm.prepare({
            "steps": [self.step],
            "container_prepare": ["/app/workload/guards/warm_git_objects.py",
                                  "--steps", "/judge-steps.json",
                                  "--inputs", "/app/workload/inputs"],
            "output": "/scratch",
        }, self.inputs)
        self.assertNotIn("example", json.dumps(result))
        self.assertEqual(result["files_read"], 2)

    def test_reads_larger_than_the_buffer_in_bounded_chunks(self):
        with patch.object(warm, "BUFFER_BYTES", 7):
            result = warm.prepare([self.step], self.inputs)
        self.assertEqual(result["bytes_read"], 41)
        self.assertEqual(result["buffer_bytes"], 7)

    def test_non_repository_commands_are_explicit_noop(self):
        result = warm.prepare([{"argv": ["python3", "gate.py"]}], self.inputs)
        self.assertEqual(result["status"], "no-repository-steps")
        self.assertEqual(result["bytes_read"], 0)

    def test_rejects_outside_repository(self):
        step = {"argv": ["git", "-C", str(self.root), "diff-tree", "--stdin"]}
        with self.assertRaises(ValueError):
            warm.prepare([step], self.inputs)

    def test_rejects_symlinked_repository(self):
        alias = self.inputs / "histories/alias"
        alias.symlink_to(self.repository, target_is_directory=True)
        step = {"argv": ["git", "-C", str(alias), "diff-tree", "--stdin"]}
        with self.assertRaises(ValueError):
            warm.prepare([step], self.inputs)

    def test_rejects_symlinked_pack_file(self):
        outside = self.root / "outside"
        outside.write_bytes(b"not canonical")
        path = self.pack / "pack-fixture.pack"
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(OSError):
            warm.prepare([self.step], self.inputs)

    def test_rejects_missing_pack_index(self):
        (self.pack / "pack-fixture.idx").unlink()
        with self.assertRaises(ValueError):
            warm.prepare([self.step], self.inputs)

    def test_rejects_unrecognized_diff_tree_invocation(self):
        step = {"argv": ["git", "-C", str(self.repository), "diff-tree", "HEAD"]}
        with self.assertRaises(ValueError):
            warm.prepare([step], self.inputs)

    def test_cli_never_executes_git(self):
        # The selected executable deliberately does not exist. Preparation is
        # only file I/O; it must neither invoke it nor compute a diff.
        step = {"argv": ["/not-an-executable", "-C", str(self.repository),
                         "diff-tree", "--stdin"]}
        request = self.root / "steps.json"
        request.write_text(json.dumps([step]))
        result = subprocess.run([sys.executable, str(HELPER), "--steps", str(request),
                                 "--inputs", str(self.inputs)],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["bytes_read"], 41)
        self.assertEqual(result.stderr, "")

    def test_cli_only_reads_protected_request(self):
        request = self.root / "judge-steps.json"
        raw = json.dumps({"steps": [self.step], "container_prepare": [],
                          "output": "/scratch"}).encode()
        request.write_bytes(raw)
        request.chmod(0o444)
        subprocess.run([sys.executable, str(HELPER), "--steps", str(request),
                        "--inputs", str(self.inputs)], check=True, capture_output=True)
        self.assertEqual(request.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
