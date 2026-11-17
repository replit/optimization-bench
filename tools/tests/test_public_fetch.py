import base64
import hashlib
import gzip
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
import zlib
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from payload_check import Fetcher, acquire, check, png_stable_sha


def pin(url, data):
    return {"kind": "url", "url": url, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


class PublicFetchTests(unittest.TestCase):
    def test_gzip_recipe_requires_registered_guard_capability(self):
        from tools import check_tasks
        root = Path(__file__).resolve().parents[2]
        manifest = {"files": [{"scope": "public", "fetch": {"kind": "gzip-member-slice"}}]}
        with self.assertRaisesRegex(AssertionError, "gzip-member-slice"):
            check_tasks.guard_version(root / "tasks/numpy-quantile/environment/payload_check.py", manifest)
        current = check_tasks.guard_version(root / "tools/payload_check.py", manifest)
        self.assertEqual(current["id"], json.loads((root / "tools/payload_guard_versions.json").read_text())["current"])

    def test_pinned_gzip_member_slice_preserves_original_framing(self):
        with tempfile.TemporaryDirectory() as temporary:
            header, body = b"immutable record header\r\n", b"original body\r\n"
            record = header + body + b"\r\n"
            leading = gzip.compress(b"previous record", mtime=0)
            member = gzip.compress(record, mtime=0)
            archive = leading + member + gzip.compress(b"following record", mtime=0)
            recipe = {
                "kind": "gzip-member-slice", "archive": pin("https://example.invalid/records.gz", archive),
                "compressed_offset": len(leading), "compressed_bytes": len(member),
                "compressed_sha256": hashlib.sha256(member).hexdigest(),
                "record_bytes": len(record), "record_sha256": hashlib.sha256(record).hexdigest(),
                "body_offset": len(header), "body_bytes": len(body)}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(archive)):
                result = Fetcher(temporary).render({"kind": "concat", "parts": [
                    {"kind": "literal", "base64": base64.b64encode(b"0\n").decode()}, recipe]})
            self.assertEqual(result, b"0\n" + body)

    def test_gzip_member_slice_rejects_wrong_commitments_bounds_and_extra_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = b"header\noriginal body"
            member = gzip.compress(record, mtime=0)
            extra = gzip.compress(b"another record", mtime=0)
            archive = member + extra
            recipe = {
                "kind": "gzip-member-slice", "archive": pin("https://example.invalid/records.gz", archive),
                "compressed_offset": 0, "compressed_bytes": len(member),
                "compressed_sha256": hashlib.sha256(member).hexdigest(),
                "record_bytes": len(record), "record_sha256": hashlib.sha256(record).hexdigest(),
                "body_offset": 7, "body_bytes": len(record) - 7}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(archive)):
                fetcher = Fetcher(temporary)
                fetcher.url(recipe["archive"])
            for change in (
                    {"compressed_sha256": "0" * 64}, {"record_sha256": "0" * 64},
                    {"record_bytes": len(record) - 1}, {"body_offset": len(record)},
                    {"body_bytes": -1}, {"compressed_offset": len(archive)},
                    {"compressed_bytes": len(archive), "compressed_sha256": hashlib.sha256(archive).hexdigest()}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    fetcher.render({**recipe, **change})

    def test_zip_and_debian_members_are_content_authenticated(self):
        with tempfile.TemporaryDirectory() as temporary:
            body = b"frozen original member"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("000/000255.doc", body)
            blob = buffer.getvalue()
            recipe = {"kind": "zip-member", "archive": pin("https://example.invalid/GovDocs1/000.zip", blob),
                      "member": "000/000255.doc", "bytes": len(body),
                      "sha256": hashlib.sha256(body).hexdigest()}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(blob)):
                self.assertEqual(Fetcher(temporary).render(recipe), body)
            recipe["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "ZIP member differs"):
                Fetcher(temporary).render(recipe)
            header = (b"data.tar.zst/".ljust(16) + b"0".ljust(12) + b"0".ljust(6) +
                      b"0".ljust(6) + b"100644".ljust(8) + str(len(body)).encode().ljust(10) + b"`\n")
            blob = b"!<arch>\n" + header + body + (b"\n" if len(body) % 2 else b"")
            recipe = {"kind": "ar-member", "archive": pin("https://example.invalid/package_1.0.deb", blob),
                      "member": "data.tar.zst", "bytes": len(body),
                      "sha256": hashlib.sha256(body).hexdigest()}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(blob)):
                self.assertEqual(Fetcher(Path(temporary) / "deb").render(recipe), body)

    def test_concat_and_archive_members_preserve_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            source = b"original source"
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                entry = tarfile.TarInfo("package/input")
                entry.size = len(source)
                archive.addfile(entry, io.BytesIO(source))
            data = buffer.getvalue()
            record = pin("https://example.invalid/package-1.0.tgz", data)
            fetcher = Fetcher(cache)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)):
                result = fetcher.render({"kind": "concat", "parts": [
                    {"kind": "literal", "base64": base64.b64encode(b"kind\n").decode()},
                    {"kind": "tar-member", "archive": record, "member": "package/input",
                     "bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()}]})
            self.assertEqual(result, b"kind\n" + source)

    def test_wrong_remote_bytes_never_become_an_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = pin("https://example.invalid/1.0.txt", b"right")
            (root / "payload-public.json").write_text(json.dumps({
                "task": "example", "files": [{"destination": "inputs/hash", "bytes": 5,
                    "sha256": record["sha256"], "fetch": record}]}))
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaisesRegex(ValueError, "size/SHA-256"):
                    acquire(root)
            self.assertFalse((root / "inputs/hash").exists())

    def test_only_recorded_png_times_may_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            signature = b"\x89PNG\r\n\x1a\n"
            frozen_time = chunk(b"tIME", b"\x07\xe9\x01\x02\x03\x04\x05")
            current_time = chunk(b"tIME", b"\x07\xea\x01\x02\x03\x04\x05")
            image = chunk(b"IDAT", b"frozen compressed image bytes")
            expected = signature + frozen_time + image + chunk(b"IEND", b"")
            current = signature + current_time + image + chunk(b"IEND", b"")
            recipe = {"kind": "png-recorded-timestamps", "source": {
                "url": "https://example.invalid/fixed-image.png", "bytes": len(current),
                "stable_sha256": png_stable_sha(expected)},
                "recorded": [{"index": 0, "base64": base64.b64encode(frozen_time).decode()}]}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(current)):
                self.assertEqual(Fetcher(temporary).render(recipe), expected)
            altered = signature + current_time + chunk(b"IDAT", b"altered compressed image!!") + chunk(b"IEND", b"")
            # A different source image is never accepted even when timestamps are restored.
            recipe["source"]["bytes"] = len(altered)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(altered)):
                with self.assertRaisesRegex(ValueError, "unavailable or differs"):
                    Fetcher(Path(temporary) / "fresh").render(recipe)
