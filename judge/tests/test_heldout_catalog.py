"""Static checkout contracts: retained bytes and references, no parser dependencies."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import heldout


class HeldoutCatalogTests(unittest.TestCase):
    def test_catalog_heldout_availability_matches_authenticated_locks(self):
        root = Path(__file__).resolve().parents[2]
        tasks = json.loads((root / "datasets/all.json").read_text())["tasks"]
        for task in tasks:
            with self.subTest(task=task):
                self.check_catalog_lock(root / "tasks" / task / "tests")

    @staticmethod
    def omitted_in_export(tests):
        """Held-out bodies a public export leaves out by design (see EXPORT.json)."""
        root = Path(tests).parents[2]
        if not (root / "EXPORT.json").is_file():
            return []
        ledger = json.loads((root / "datasets/public-release-ledger.json").read_text())
        paths = ledger["tasks"].get(Path(tests).parent.name, {}).get("omit_from_export", [])
        return [path for path in paths if not (root / path).exists()]

    def check_catalog_lock(self, tests):
        if self.omitted_in_export(tests):
            # Scoring must refuse, and say why, until the omitted bodies are recovered.
            with self.assertRaisesRegex(ValueError, "Held-out file not present"):
                heldout.check(tests, allow_missing_references=True)
            return
        metadata = json.loads((tests / "heldout.lock.json").read_text())
        unfetched = False
        for name in metadata["files"]:
            path = heldout.contained(tests, name)
            if path.is_file():
                with path.open("rb") as stream:
                    if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"):
                        unfetched = True
                        break
        if unfetched:
            # Static validation must still authenticate every retained byte,
            # not treat LFS pointer text as content.
            for require_available in (False, True):
                with self.assertRaisesRegex(ValueError, "git lfs pull"):
                    heldout.check(tests, require_available=require_available,
                                  allow_missing_references=True)
            return
        lock = heldout.check(tests, allow_missing_references=True)
        if lock["status"] == "available":
            heldout.check(tests, require_available=True, allow_missing_references=True)
        else:
            with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
                heldout.check(tests, require_available=True, allow_missing_references=True)

    def test_static_catalog_authenticates_fetch_only_task_without_acquiring_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "reference-task"
            tests = task / "tests"
            (tests / "heldout/expected").mkdir(parents=True)
            digest = hashlib.sha256(b"reference input").hexdigest()
            name = "heldout/files/" + digest
            entry = {"kind": "file", "scope": "heldout", "role": "input",
                     "license_basis": "fetch-only; no redistribution",
                     "url": "https://example.org/pinned/input",
                     "bytes": len(b"reference input"), "sha256": digest}
            manifest = tests / "heldout/manifest.json"
            manifest.write_text(json.dumps({"splits": {"private": [{"docs": [{
                "sha256": digest, "expect_sha": hashlib.sha256(b"expected").hexdigest(),
                "expect_len": len(b"expected")}]}]}}))
            expected = tests / "heldout/expected/output"
            expected.write_bytes(b"expected")
            lock = {"schema_version": 1, "status": "available", "references": {name: entry},
                    "files": {"heldout/manifest.json": heldout.sha(manifest),
                              "heldout/expected/output": heldout.sha(expected)}}
            lock_path = tests / "heldout.lock.json"
            lock_path.write_text(json.dumps(lock))
            payload = {"files": [{**entry, "name": "input", "destination": "tests/" + name,
                                   "retention": "fetch-only"}]}
            payload_path = task / "payload.json"
            payload_path.write_text(json.dumps(payload))
            with patch.object(heldout.urllib.request, "urlopen") as fetch:
                self.check_catalog_lock(tests)
                self.assertFalse((tests / name).exists())
                with self.assertRaisesRegex(ValueError, "unavailable or mismatched"):
                    heldout.check(tests, require_available=True)
                # Static validation cannot waive retained-output authentication.
                expected.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    self.check_catalog_lock(tests)
                expected.write_bytes(b"expected")
                # Nor may it waive payload/lock agreement or license metadata.
                payload["files"][0]["bytes"] += 1
                payload_path.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, "pins differ"):
                    self.check_catalog_lock(tests)
                payload["files"][0]["bytes"] -= 1
                payload_path.write_text(json.dumps(payload))
                del entry["license_basis"]
                lock_path.write_text(json.dumps(lock))
                with self.assertRaisesRegex(ValueError, "license basis"):
                    self.check_catalog_lock(tests)
                fetch.assert_not_called()
