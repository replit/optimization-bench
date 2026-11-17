import json
from pathlib import Path
import tempfile
import hashlib
import unittest
import sys

from tools.fuzz_runners import cli_faults as faults

sys.path.insert(0, str(faults.ROOT / "tools/runner"))
from runner import Step


class FaultValidationTests(unittest.TestCase):
    def test_secret_environment_rejected_by_name_without_value(self):
        with self.assertRaises(faults.FaultHarnessError) as error:
            faults._step_dict(Step(["program"], env={"API_KEY": "do-not-persist"}))
        self.assertIn("API_KEY", str(error.exception))
        self.assertNotIn("do-not-persist", str(error.exception))

    def test_relative_watch_paths_and_source_escape(self):
        for value in ("../input", "/etc/passwd", ""):
            with self.assertRaises(faults.FaultHarnessError):
                faults._relative(value)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "escape").symlink_to("/etc")
            with self.assertRaises(faults.FaultHarnessError):
                faults._inside(root, "escape/passwd")

    def test_symlinks_recorded_without_reading_external_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "escape").symlink_to("/etc/passwd")
            data = faults.snapshot(root, ["escape"])["escape"]["escape"]
            self.assertEqual(data["kind"], "symlink")
            self.assertNotIn("sha256", data)

    def test_side_effect_hashes_distinguish_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.sql").write_bytes(b"select 1;")
            before = faults.snapshot(root, ["input.sql"])
            (root / "input.sql").write_bytes(b"select 2;")
            self.assertNotEqual(before, faults.snapshot(root, ["input.sql"]))

    def test_ancestor_symlink_never_followed_by_watch_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "escape").symlink_to("/etc")
            data = faults.snapshot(root, ["escape/passwd"])["escape/passwd"]["escape/passwd"]
            self.assertEqual(data["kind"], "ancestor_symlink")
            self.assertNotIn("sha256", data)

    def test_render_same_argv_for_both_builds(self):
        self.assertEqual(faults._render("{build}/prog", "/baseline", "/candidate"), "/app/build/prog")
        self.assertEqual(faults._render("/baseline/prog", "/baseline", "/candidate"), "/app/build/prog")
        self.assertEqual(faults._render("/candidate/prog", "/baseline", "/candidate"), "/app/build/prog")

    def test_json_observer_never_uses_faulted_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.write_text("retained")
            (root / "observer.json").symlink_to(outside)
            faults._publish(root, {"exit_code": -13, "candidate_verdict": None})
            self.assertEqual(outside.read_text(), "retained")
            self.assertEqual(json.loads((root / "observer.json").read_text())["exit_code"], -13)

    def test_no_universal_exit_code_or_blank_stderr_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stderr.bin").write_bytes(b"")
            result = faults._stream(root / "stderr.bin")
            self.assertEqual(result["bytes"], 0)
            self.assertEqual(result["headline"], "")
            self.assertNotIn("passed", result)

    def test_full_raw_stream_survives_temporary_source_cleanup(self):
        with tempfile.TemporaryDirectory() as durable:
            out = Path(durable)
            content = b"Error headline\n" + b"frame data\n" * 7000 + b"input.sql:17:9 TAIL\n"
            with tempfile.TemporaryDirectory() as transient:
                source = Path(transient)
                (source / "stdout.bin").write_bytes(b"")
                (source / "stderr.bin").write_bytes(content)
                row = {"status": "observed", "stdout": faults._stream(source / "stdout.bin"),
                       "stderr": faults._stream(source / "stderr.bin")}
                self.assertTrue(row["stderr"]["head_truncated"])
                faults._retain_streams(row, source, out, "../case unsafe", "dev_full", "baseline", 1024 * 1024)
            artifact = row["stderr"]["artifact"]
            self.assertEqual(Path(artifact["path"]).read_bytes(), content)
            self.assertEqual(artifact["sha256"], hashlib.sha256(content).hexdigest())
            self.assertTrue(Path(artifact["path"]).is_relative_to(out))
            self.assertTrue(artifact["complete"])
            self.assertIn(b"input.sql:17:9 TAIL", Path(artifact["path"]).read_bytes())

    def test_raw_hash_mismatch_is_not_normalizable_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "source", Path(tmp) / "out"
            source.mkdir()
            (source / "stdout.bin").write_bytes(b"")
            (source / "stderr.bin").write_bytes(b"original")
            row = {"status": "observed", "stdout": faults._stream(source / "stdout.bin"),
                   "stderr": faults._stream(source / "stderr.bin")}
            (source / "stderr.bin").write_bytes(b"modified")
            with self.assertRaises(faults.FaultHarnessError):
                faults._retain_streams(row, source, out, "case", "dev_full", "baseline", 65536)
            self.assertFalse(list(out.rglob("stderr.bin")))

    def test_raw_bound_is_not_silent_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "source", Path(tmp) / "out"
            source.mkdir()
            (source / "stdout.bin").write_bytes(b"")
            (source / "stderr.bin").write_bytes(b"x" * 65537)
            row = {"status": "observed", "stdout": faults._stream(source / "stdout.bin"),
                   "stderr": faults._stream(source / "stderr.bin")}
            with self.assertRaises(faults.FaultHarnessError):
                faults._retain_streams(row, source, out, "case", "dev_full", "baseline", 65536)
            self.assertFalse(list(out.rglob("stderr.bin")))


if __name__ == "__main__":
    unittest.main()
