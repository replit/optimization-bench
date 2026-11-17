"""Build-time unavailable locks must not invent data or weaken archive checks."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from judge import heldout

ROOT = Path(__file__).resolve().parents[2]


class HeldoutArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tests = self.root / "task/tests"
        (self.tests / "heldout").mkdir(parents=True)
        self.destination = self.root / "projects"
        self.manifest = self.tests / "heldout/manifest.json"
        self.manifest.write_text(json.dumps({"splits": {}}))
        self.lock = {"schema_version": 1, "status": "unavailable",
                     "files": {"heldout/manifest.json": heldout.sha(self.manifest)},
                     "reason": "Original private corpus is unavailable."}
        self.save()

    def save(self):
        (self.tests / "heldout.lock.json").write_text(json.dumps(self.lock))

    def inventory(self):
        return {p.relative_to(self.tests).as_posix(): p.read_bytes()
                for p in self.tests.rglob("*") if p.is_file()}

    def available(self):
        content = b"offline held-out fixture\n"
        digest = hashlib.sha256(content).hexdigest()
        name = "heldout/files/" + digest
        file = self.tests / name
        file.parent.mkdir()
        file.write_bytes(content)
        self.manifest.write_text(json.dumps({"splits": {"private": [{"docs": [
            {"sha256": digest, "expect_sha": "a" * 64, "expect_len": 1}]}]}}))
        self.lock.update(status="available", files={
            "heldout/manifest.json": heldout.sha(self.manifest), name: digest})
        self.lock.pop("reason")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as stream:
            directory = tarfile.TarInfo("source")
            directory.type = tarfile.DIRTYPE
            stream.addfile(directory)
            entry = tarfile.TarInfo("source/input.txt")
            entry.size = len(content)
            stream.addfile(entry, io.BytesIO(content))
        blob = archive.getvalue()
        self.lock["archives"] = [{
            "url": "https://example.org/pinned/archive.tar",
            "sha256": hashlib.sha256(blob).hexdigest(), "project": "fixture-project"}]
        self.save()
        return blob, content

    def test_unavailable_without_archives_fetches_nothing_and_retains_files(self):
        before = self.inventory()
        output = io.StringIO()
        with patch.object(heldout.urllib.request, "urlopen") as fetch, contextlib.redirect_stdout(output):
            heldout.fetch_archives(self.tests, self.destination)
        fetch.assert_not_called()
        self.assertEqual(output.getvalue(), "heldout unavailable: " + self.lock["reason"] + "\n")
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.destination.exists())
        # Build success does not authorize a scored run.
        with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
            heldout.check(self.tests, require_available=True)

    def test_unavailable_still_validates_committed_files_before_noop(self):
        self.manifest.write_text('{"splits":{"tampered":[]}}')
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            with self.assertRaisesRegex(ValueError, "Held-out hash mismatch"):
                heldout.fetch_archives(self.tests, self.destination)
        fetch.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_unavailable_template_cli_exits_zero_without_archives_or_new_files(self):
        # Keep the unavailable-template regression independent of which real
        # tasks have since recovered their authenticated held-out bodies.
        template = heldout.check(self.tests)
        self.assertNotIn("archives", template)
        self.assertEqual(template["status"], "unavailable")
        before = self.inventory()
        result = subprocess.run(
            [sys.executable, "-m", "judge.heldout", str(self.tests), str(self.destination)],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "heldout unavailable: " + template["reason"] + "\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.destination.exists())

    def test_available_committed_bodies_without_archives_need_no_network(self):
        self.available()
        self.lock["archives"] = []
        self.save()
        before = self.inventory()
        result = subprocess.run(
            [sys.executable, "-m", "judge.heldout", str(self.tests), str(self.destination)],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.inventory(), before)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_available_still_requires_archives_key(self):
        self.available()
        del self.lock["archives"]
        self.save()
        heldout.check(self.tests, require_available=True)
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            with self.assertRaisesRegex(KeyError, "archives"):
                heldout.fetch_archives(self.tests, self.destination)
        fetch.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_available_download_requires_matching_archive_hash(self):
        blob, content = self.available()
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(blob)) as fetch:
            heldout.fetch_archives(self.tests, self.destination)
        fetch.assert_called_once_with("https://example.org/pinned/archive.tar", timeout=180)
        self.assertEqual((self.destination / "fixture-project/input.txt").read_bytes(), content)
        shutil.rmtree(self.destination)
        self.lock["archives"][0]["sha256"] = "0" * 64
        self.save()
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(blob)):
            with self.assertRaisesRegex(ValueError, "Archive checksum mismatch"):
                heldout.fetch_archives(self.tests, self.destination)
        self.assertFalse((self.destination / "fixture-project").exists())

    def test_available_still_requires_public_https_url(self):
        self.available()
        for url in ["http://example.org/archive.tar", "https://user@example.org/archive.tar"]:
            self.lock["archives"][0]["url"] = url
            self.save()
            with self.subTest(url=url), patch.object(heldout.urllib.request, "urlopen") as fetch:
                with self.assertRaisesRegex(ValueError, "Expected public HTTPS archive"):
                    heldout.fetch_archives(self.tests, self.destination)
                fetch.assert_not_called()

    def test_unknown_status_is_error_not_noop_or_scoring_permission(self):
        self.lock["status"] = "unexpected"
        self.save()
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            with self.assertRaisesRegex(ValueError, "Unknown held-out lock status"):
                heldout.check(self.tests)
            with self.assertRaisesRegex(ValueError, "Unknown held-out lock status"):
                heldout.fetch_archives(self.tests, self.destination)
        fetch.assert_not_called()
        self.assertFalse(self.destination.exists())
