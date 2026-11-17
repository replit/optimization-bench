"""Stale metadata transport pins must fail without recapturing expected outputs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_tasks import check_manifest_pins, check_task


class ManifestPinTests(unittest.TestCase):
    def fixture(self, root):
        task = Path(root) / "task"
        path = task / "tests/heldout/manifest.json"
        path.parent.mkdir(parents=True)
        content = b'{"cases":[]}\n'
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        payload = {"files": [{"destination": "tests/heldout/manifest.json",
                              "bytes": len(content), "sha256": digest,
                              "scope": "heldout"}]}
        (task / "payload.json").write_text(json.dumps(payload))
        (task / "tests/heldout.lock.json").write_text(json.dumps({
            "files": {"heldout/manifest.json": digest}}))
        return task, path, payload

    def test_matching_payload_and_lock_pins_pass(self):
        with tempfile.TemporaryDirectory() as root:
            task, _, payload = self.fixture(root)
            check_manifest_pins(task, payload)

    def test_stale_size_fails_direct_task_validation(self):
        with tempfile.TemporaryDirectory() as root:
            task, path, _ = self.fixture(root)
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "Manifest payload size/SHA256 mismatch"):
                check_task(task)

    def test_same_size_changed_content_fails(self):
        with tempfile.TemporaryDirectory() as root:
            task, path, payload = self.fixture(root)
            path.write_bytes(path.read_bytes().replace(b"cases", b"CASES"))
            with self.assertRaisesRegex(ValueError, "Manifest payload size/SHA256 mismatch"):
                check_manifest_pins(task, payload)

    def test_stale_lock_fails_even_when_payload_pin_matches(self):
        with tempfile.TemporaryDirectory() as root:
            task, _, payload = self.fixture(root)
            (task / "tests/heldout.lock.json").write_text(json.dumps({
                "files": {"heldout/manifest.json": "0" * 64}}))
            with self.assertRaisesRegex(ValueError, "Manifest held-out lock SHA256 mismatch"):
                check_manifest_pins(task, payload)
