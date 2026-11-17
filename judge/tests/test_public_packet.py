import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import public_input_preflight, public_packet


def entry(name, body):
    return {"destination": "environment/workload/" + name, "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


class PublicPacketTests(unittest.TestCase):
    def fixture(self, root):
        task = root / "task"
        environment = task / "environment"
        environment.mkdir(parents=True)
        raw = entry("inputs/archive/original.gz", b"original-archive")
        derived = entry("inputs/data/original.jsonl", b"exact-original-derived-output")
        original = task / raw["destination"]
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original-archive")
        (task / "task.toml").write_text("[environment]\ncpus=8\nmemory_mb=16384\n")
        declaration = {"schema": public_packet.SCHEMA, "mounts": ["inputs/data"],
            "derived_inputs": [derived], "prepare": {
                "image": "python:3.12-slim-bookworm@sha256:" + "a" * 64,
                "argv": ["python3", "/payload/prepare.py", "/payload"],
                "write_dirs": ["workload/inputs/data"]}}
        path = environment / public_packet.NAME
        path.write_text(json.dumps(declaration))
        return task, raw, derived, public_packet.load(task)

    def test_absent_declaration_preserves_ordinary_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(public_packet.load(Path(temporary)))

    def test_controller_prepares_original_bytes_with_scoped_writes_and_no_raw_runtime_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, raw, derived, packet = self.fixture(root)
            cache = root / "runtime"
            def prepare(command, **kwargs):
                target = task / derived["destination"]
                target.write_bytes(b"exact-original-derived-output")
                self.assertTrue(kwargs["check"])
                self.assertIn("--read-only", command)
                self.assertEqual(command[command.index("--network") + 1], "none")
                self.assertEqual(command[command.index("--cpus") + 1], "8")
                self.assertEqual(command[command.index("--memory") + 1], "16384m")
                mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
                self.assertTrue(any("target=/payload,readonly" in value for value in mounts))
                self.assertTrue(any("target=/payload/workload/inputs/data" in value
                                    and "readonly" not in value for value in mounts))
            with patch.object(public_packet.subprocess, "run", side_effect=prepare) as run:
                self.assertEqual(public_packet.ensure_derived(task, packet, fetch=True, source=cache), 1)
                run.assert_called_once()
            self.assertEqual(public_input_preflight._runtime_workload(task, [raw, derived], cache, packet), 1)
            self.assertFalse((cache / "inputs/archive/original.gz").exists())
            self.assertEqual((cache / "inputs/data/original.jsonl").read_bytes(),
                             b"exact-original-derived-output")

    def test_read_only_check_never_prepares_or_restores_missing_derived_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, _, _, packet = self.fixture(Path(temporary))
            with patch.object(public_packet.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "missing"):
                    public_packet.ensure_derived(task, packet, fetch=False)
                run.assert_not_called()
            self.assertFalse((task / "environment/workload/inputs/data").exists())

    def test_corrupt_derived_cache_fails_without_reacquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, _, _, packet = self.fixture(root)
            cache = root / "runtime"
            target = cache / "inputs/data/original.jsonl"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"corrupt")
            with patch.object(public_packet.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "cache differs"):
                    public_packet.ensure_derived(task, packet, fetch=True, source=cache)
                run.assert_not_called()

    def test_existing_exact_runtime_archive_is_rejected_as_wrong_visibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, raw, _, packet = self.fixture(root)
            cache = root / "runtime"
            target = cache / "inputs/archive/original.gz"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original-archive")
            with self.assertRaisesRegex(public_input_preflight.PublicInputsError, "Acquisition-only"):
                public_input_preflight._runtime_workload(task, [raw], cache, packet)

    def test_metadata_cannot_widen_write_scope_escape_paths_or_drop_image_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, _, _, _ = self.fixture(Path(temporary))
            path = task / "environment" / public_packet.NAME
            original = json.loads(path.read_text())
            variants = []
            for key, value in (("image", "python:latest"), ("write_dirs", ["workload"]),
                               ("write_dirs", ["../elsewhere"]), ("argv", ["python3", "\0"])):
                changed = copy.deepcopy(original)
                changed["prepare"][key] = value
                variants.append(changed)
            changed = copy.deepcopy(original)
            changed["mounts"] = ["inputs/files"]
            variants.append(changed)
            for changed in variants:
                with self.subTest(value=changed):
                    path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        public_packet.load(task)

    def test_preparer_must_reproduce_original_derived_commitment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, _, derived, packet = self.fixture(root)
            def prepare(*args, **kwargs):
                (task / derived["destination"]).write_bytes(b"normalized-but-wrong")
            with patch.object(public_packet.subprocess, "run", side_effect=prepare):
                with self.assertRaisesRegex(ValueError, "original bytes"):
                    public_packet.ensure_derived(task, packet, fetch=True)


if __name__ == "__main__":
    unittest.main()
