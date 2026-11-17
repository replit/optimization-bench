"""Synthetic write-plan regressions, without private data or SQLGlot imports."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("alternate_adoption", Path(__file__).with_name("adopt.py"))
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)


class WritePlanTests(unittest.TestCase):
    def test_authoring_rejects_unfixed_and_ignored_seeds_before_any_work(self):
        for seed, ignored, randomized in (("1", 0, 0), ("0", 1, 0), ("0", 0, 1)):
            with mock.patch.dict(adoption.os.environ, {"PYTHONHASHSEED": seed}), \
                    mock.patch.object(adoption.sys, "flags", SimpleNamespace(ignore_environment=ignored, hash_randomization=randomized)):
                with self.assertRaisesRegex(ValueError, "PYTHONHASHSEED"):
                    adoption.adopt(None)

    def test_existing_input_cannot_be_overwritten_even_with_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"original")
            for freeze in (False, True):
                with self.assertRaises(ValueError):
                    adoption.plan(path, b"changed", freeze, {})
                self.assertEqual(path.read_bytes(), b"original")

    def test_missing_input_requires_freeze_and_planning_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            with self.assertRaises(ValueError):
                adoption.plan(path, b"new", False, {})
            writes = {}
            adoption.plan(path, b"new", True, writes)
            self.assertEqual(writes, {path: b"new"})
            self.assertFalse(path.exists())

    def test_read_only_verification_cannot_change_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest"
            path.write_bytes(b"original")
            with self.assertRaises(ValueError):
                adoption.plan(path, b"changed", False, {}, metadata=True)
            writes = {}
            adoption.plan(path, b"changed", True, writes, metadata=True)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(writes[path], b"changed")

    def test_adoption_rejects_inherited_corruption_before_any_write(self):
        for corruption in ("public_manifest", "private_manifest", "private_lock", "missing_lock", "payload"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                public = "environment/workload/inputs/manifest.json"
                private = "tests/heldout/manifest.json"
                manifest = {"splits": {"train": [{"docs": [{"path": "old", "expect_sha": "original"}]}]}}
                encoded = adoption.dump(manifest)
                lock = {"files": {"heldout/manifest.json": adoption.sha(encoded)}}
                payload = {"files": [{"destination": private, "bytes": len(encoded),
                                      "sha256": adoption.sha(encoded)}]}
                anchor = {"manifests": {name: {"canonical_sha256": adoption.sha(encoded)}
                                        for name in (public, private)}}
                files = {public: encoded, private: encoded,
                         "public/alternates/inherited.lock.json": adoption.dump(anchor),
                         "tests/heldout.lock.json": adoption.dump(lock), "payload.json": adoption.dump(payload)}
                if corruption in ("public_manifest", "private_manifest"):
                    changed = json.loads(encoded)
                    changed["splits"]["train"][0]["docs"][0]["expect_sha"] = "changed"
                    files[public if corruption == "public_manifest" else private] = adoption.dump(changed)
                elif corruption in ("private_lock", "missing_lock"):
                    if corruption == "private_lock":
                        lock["files"]["heldout/manifest.json"] = "0" * 64
                    else:
                        lock["files"].clear()
                    files["tests/heldout.lock.json"] = adoption.dump(lock)
                else:
                    payload["files"][0]["bytes"] += 1
                    files["payload.json"] = adoption.dump(payload)
                for relative, raw in files.items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                with mock.patch.object(adoption, "TASK", root), \
                        mock.patch.dict(adoption.os.environ, {"PYTHONHASHSEED": "0"}), \
                        mock.patch.object(adoption.sys, "flags", SimpleNamespace(ignore_environment=0, hash_randomization=0)), \
                        mock.patch.object(adoption, "baseline") as baseline:
                    with self.assertRaisesRegex(ValueError, "commitment"):
                        adoption.adopt(SimpleNamespace(freeze=True))
                    baseline.assert_not_called()
                after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
