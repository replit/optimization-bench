import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "stage_payload", Path(__file__).resolve().parents[1] / "stage_payload.py")
payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payload)


class PayloadTests(unittest.TestCase):
    def heldout_fixture(self, temporary):
        task, source = [Path(temporary) / name for name in ("task", "source")]
        task.mkdir()
        source.mkdir()
        raw = b"payload"
        entry = {"name": "blob", "destination": "tests/heldout/files/blob",
                 "scope": "heldout", "bytes": len(raw),
                 "sha256": hashlib.sha256(raw).hexdigest()}
        (task / "payload.json").write_text(json.dumps({"files": [entry]}))
        pointer = (f"version https://git-lfs.github.com/spec/v1\n"
                   f"oid sha256:{entry['sha256']}\nsize {len(raw)}\n").encode()
        target = task / entry["destination"]
        target.parent.mkdir(parents=True)
        target.write_bytes(pointer)
        target.chmod(0o640)
        (source / "blob").write_bytes(raw)
        return task, source, target, pointer

    def test_local_source_stages_over_unfetched_heldout_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, source, target, _ = self.heldout_fixture(temporary)
            with patch.object(payload.subprocess, "run") as external:
                self.assertEqual(payload.stage(task, source=source, scope="heldout"), 1)
            external.assert_not_called()
            self.assertEqual(target.read_bytes(), b"payload")
            payload.check(task, "heldout")
            self.assertEqual(payload.stage(task, source=source, scope="heldout"), 0)

    def test_heldout_pointer_rejected_without_local_only_source(self):
        for operation in ("check", "stage", "fetch", "source-and-fetch"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                task, source, target, pointer = self.heldout_fixture(temporary)
                with self.assertRaisesRegex(ValueError, "held-out LFS object not fetched"):
                    if operation == "check":
                        payload.check(task, "heldout")
                    else:
                        payload.stage(task, scope="heldout",
                                      source=source if operation == "source-and-fetch" else None,
                                      fetch=operation in ("fetch", "source-and-fetch"))
                self.assertEqual(target.read_bytes(), pointer)

    def test_local_source_rejects_invalid_heldout_payload(self):
        for invalid in ("pointer", "wrong-sha", "wrong-size", "missing", "symlink"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                task, source, target, pointer = self.heldout_fixture(temporary)
                original = source / "blob"
                if invalid == "pointer":
                    original.write_bytes(pointer)
                    message = "held-out LFS object not fetched"
                elif invalid in ("wrong-sha", "wrong-size"):
                    original.write_bytes(b"PAYLOAD" if invalid == "wrong-sha" else b"short")
                    message = "size/SHA-256 mismatch"
                else:
                    original.unlink()
                    message = "Missing source"
                    if invalid == "symlink":
                        (source / "real").write_bytes(b"payload")
                        original.symlink_to(source / "real")
                        message = "Symlink"
                with self.assertRaisesRegex(ValueError, message):
                    payload.stage(task, source=source, scope="heldout")
                self.assertEqual(target.read_bytes(), pointer)
                self.assertEqual(target.stat().st_mode & 0o777, 0o640)
                self.assertFalse(list(task.glob(".payload-transaction-*")))

    def test_local_heldout_stage_rejects_destination_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, source, target, pointer = self.heldout_fixture(temporary)
            original = task / "original"
            original.write_bytes(pointer)
            target.unlink()
            target.symlink_to(original)
            with self.assertRaisesRegex(ValueError, "Symlink"):
                payload.stage(task, source=source, scope="heldout")
            self.assertTrue(target.is_symlink())
            self.assertEqual(original.read_bytes(), pointer)

    def test_failed_local_heldout_stage_restores_pointer_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, source, target, pointer = self.heldout_fixture(temporary)
            manifest = json.loads((task / "payload.json").read_text())
            for name in ("created", "missing"):
                raw = name.encode()
                manifest["files"].append({
                    "name": name, "destination": "tests/heldout/files/" + name,
                    "scope": "heldout", "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()})
            (task / "payload.json").write_text(json.dumps(manifest))
            (source / "created").write_bytes(b"created")
            with self.assertRaisesRegex(ValueError, "Missing source"):
                payload.stage(task, source=source, scope="heldout")
            self.assertEqual(target.read_bytes(), pointer)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertFalse((target.parent / "created").exists())
            self.assertFalse((target.parent / "missing").exists())
            self.assertFalse(list(task.glob(".payload-transaction-*")))

    def test_failed_multi_file_stage_rolls_back_created_and_replaced_payloads(self):
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting), tempfile.TemporaryDirectory() as temporary:
                task, source = [Path(temporary) / name for name in ("task", "source")]
                task.mkdir()
                source.mkdir()
                entries = [
                    {"name": name, "destination": "environment/" + name, "scope": "public",
                     "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                    for name, raw in (("first", b"new"), ("second", b"missing"))]
                (task / "payload.json").write_text(json.dumps({"files": entries}))
                (source / "first").write_bytes(b"new")
                target = task / "environment/first"
                if preexisting:
                    target.parent.mkdir()
                    target.write_bytes(b"old")
                    target.chmod(0o640)
                with self.assertRaisesRegex(ValueError, "Missing source"):
                    payload.stage(task, source=source)
                if preexisting:
                    self.assertEqual(target.read_bytes(), b"old")
                    self.assertEqual(target.stat().st_mode & 0o777, 0o640)
                else:
                    self.assertFalse(target.exists())
                self.assertFalse((task / "environment/second").exists())
                self.assertFalse(list(task.glob(".payload-transaction-*")))

    def test_cli_stages_only_requested_task_without_refreshing_other_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "tasks/selected"
            task.mkdir(parents=True)
            source = root / "source"
            source.mkdir()
            (source / "data").write_bytes(b"data")
            (task / "payload.json").write_text(json.dumps({"files": [{
                "name": "data", "destination": "environment/data", "scope": "public",
                "bytes": 4, "sha256": hashlib.sha256(b"data").hexdigest()}]}))
            other = root / "tasks/unhydrated"
            other.mkdir()
            (other / "provenance.json").write_text("not hydrated; must never be parsed")
            before = (other / "provenance.json").read_bytes()
            with patch.object(payload, "ROOT", root), \
                    patch.object(payload.sys, "argv", [
                        "stage_payload.py", "--task", "selected", "--from", str(source)]), \
                    patch.object(payload.subprocess, "run") as external:
                payload.main()
            external.assert_not_called()
            self.assertEqual((other / "provenance.json").read_bytes(), before)
            self.assertEqual((task / "environment/data").read_bytes(), b"data")

    def test_generic_fetch_recipe_is_authenticated_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "task"
            task.mkdir()
            entry = {"name": "blob", "destination": "environment/blob", "scope": "public",
                     "bytes": 6, "sha256": hashlib.sha256(b"public").hexdigest(),
                     "fetch": {"kind": "url", "url": "https://example.invalid/release-1/blob",
                               "bytes": 6, "sha256": hashlib.sha256(b"public").hexdigest()}}
            (task / "payload.json").write_text(json.dumps({"files": [entry]}))
            with patch.object(payload, "ROOT", Path(temporary)), \
                    patch("payload_check.Fetcher.render", return_value=b"public"):
                self.assertEqual(payload.stage(task, fetch=True), 1)
            self.assertEqual((task / "environment/blob").read_bytes(), b"public")
            (task / "environment/blob").unlink()
            with patch.object(payload, "ROOT", Path(temporary)), \
                    patch("payload_check.Fetcher.render", return_value=b"wrong!"):
                with self.assertRaisesRegex(ValueError, "size/SHA-256"):
                    payload.stage(task, fetch=True)
            self.assertFalse((task / "environment/blob").exists())

    def test_stage_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, source = root / "task", root / "source"
            task.mkdir()
            source.mkdir()
            (source / "blob").write_bytes(b"payload")
            entry = {"name": "blob", "destination": "environment/workload/inputs/blob",
                     "sha256": hashlib.sha256(b"payload").hexdigest(),
                     "bytes": 7, "scope": "public"}
            (task / "payload.json").write_text(json.dumps({"files": [entry]}))
            self.assertEqual(len(payload.missing(task)), 1)
            self.assertEqual(payload.stage(task, source), 1)
            self.assertEqual(payload.stage(task, source), 0)
            (task / entry["destination"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
                payload.check(task)

    def test_paths_and_uncommitted_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            with self.assertRaises(ValueError):
                payload.contained(task, "../escape")
            (task / "payload.json").write_text(json.dumps({"files": [{
                "name": "operator-manifest", "destination": "tests/heldout/operator-manifest.json",
                "bytes": None, "sha256": None, "scope": "heldout"}]}))
            with self.assertRaisesRegex(ValueError, "original operator manifest/hash"):
                payload.stage(task, task)

    def test_mismatched_download_never_replaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, source = root / "task", root / "source"
            task.mkdir()
            source.mkdir()
            (source / "blob").write_bytes(b"wrong")
            (task / "payload.json").write_text(json.dumps({"files": [{
                "name": "blob", "destination": "blob", "bytes": 7,
                "sha256": hashlib.sha256(b"payload").hexdigest(), "scope": "public"}]}))
            with self.assertRaisesRegex(ValueError, "mismatch"):
                payload.stage(task, source)
            self.assertFalse((task / "blob").exists())

    def test_source_and_destination_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "original").write_bytes(b"public")
            (root / "alias").symlink_to(root / "original")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                payload.contained(root, "alias")
            (root / "real").mkdir()
            (root / "alias-directory").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Symlink"):
                payload.contained(root, "alias-directory/new-file")
