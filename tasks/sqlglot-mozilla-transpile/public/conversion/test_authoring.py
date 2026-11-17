"""Synthetic publication and immutable-reference regressions."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("conversion_authoring", Path(__file__).with_name("prepare_private.py"))
author = importlib.util.module_from_spec(spec)
spec.loader.exec_module(author)


def fixture(root):
    manifest = {"splits": {"train": [{"docs": [{"path": "original", "expect_sha": "unchanged"}]}]}}
    raw = author.dump(manifest)
    public, private = "environment/workload/inputs/manifest.json", "tests/heldout/manifest.json"
    files = {
        public: raw, private: raw,
        "payload.json": author.dump({"files": [{"destination": private, "bytes": len(raw), "sha256": author.sha(raw)}]}),
        "tests/heldout.lock.json": author.dump({"files": {"heldout/manifest.json": author.sha(raw)}}),
        "public/conversion/inherited.lock.json": author.dump({
            "manifests": {public: author.sha(raw), private: author.sha(raw)},
            "private_files_sha256": author.fingerprint({}), "payload_entries_sha256": author.fingerprint([])}),
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class AuthoringTests(unittest.TestCase):
    def test_freeze_then_read_only_verify_and_reject_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            selected = [{"source_path": "sql/private/example/view.sql", "raw": b"SELECT 3", "expected": b'["SELECT 3"]'}]
            with patch.object(author, "TASK", root):
                author.freeze(selected, {"source": "fixture"}, True)
                frozen = snapshot(root)
                author.freeze(selected, {"source": "fixture"}, False)
                self.assertEqual(frozen, snapshot(root))
                for field, value in (("raw", b"SELECT 4"), ("expected", b'["SELECT 4"]'), ("source_path", "changed")):
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        author.freeze([{**selected[0], field: value}], {"source": "fixture"}, True)
                    self.assertEqual(frozen, snapshot(root))

    def test_inherited_corruption_rejects_before_any_publication(self):
        for target in ("public", "private", "lock", "payload"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture(root)
                path = root / {"public": "environment/workload/inputs/manifest.json",
                               "private": "tests/heldout/manifest.json",
                               "lock": "tests/heldout.lock.json", "payload": "payload.json"}[target]
                value = json.loads(path.read_text())
                if target in ("public", "private"):
                    value["splits"]["train"][0]["docs"][0]["expect_sha"] = "changed"
                elif target == "lock":
                    value["files"].clear()
                else:
                    value["files"][0]["bytes"] += 1
                path.write_bytes(author.dump(value))
                before = snapshot(root)
                with patch.object(author, "TASK", root), self.assertRaises(ValueError):
                    author.freeze([], {}, True)
                self.assertEqual(before, snapshot(root))

    def test_rejects_ignored_or_late_seed_changes(self):
        for ignored, randomized in ((1, 0), (0, 1)):
            with patch.dict(author.os.environ, {"PYTHONHASHSEED": "0"}), \
                    patch.object(author.sys, "flags", SimpleNamespace(ignore_environment=ignored, hash_randomization=randomized)):
                with self.assertRaisesRegex(ValueError, "PYTHONHASHSEED"):
                    author.main(None)

    def test_resigning_an_original_file_does_not_authorize_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            original = root / "tests/heldout/original.json"
            original.write_bytes(b"old")
            lock = {"files": {"heldout/manifest.json": author.sha((root / "tests/heldout/manifest.json").read_bytes()),
                              "heldout/original.json": author.sha(b"old")}}
            anchor = root / "public/conversion/inherited.lock.json"
            value = json.loads(anchor.read_text())
            value["private_files_sha256"] = author.fingerprint({"heldout/original.json": author.sha(b"old")})
            anchor.write_bytes(author.dump(value))
            original.write_bytes(b"changed")
            lock["files"]["heldout/original.json"] = author.sha(b"changed")
            (root / "tests/heldout.lock.json").write_bytes(author.dump(lock))
            before = snapshot(root)
            with patch.object(author, "TASK", root), self.assertRaisesRegex(ValueError, "Original private"):
                author.freeze([], {}, True)
            self.assertEqual(before, snapshot(root))


if __name__ == "__main__":
    unittest.main()
