import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from email.utils import formatdate
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import payload_check as guard
import stage_payload as staging
import release_payload_guard as releases


class PublicHttpRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = b"pinned public data"
        self.record = {"url": "https://example.invalid/frozen/data",
                       "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}
        self.fetcher = guard.Fetcher(self.root / "cache")
        self.network = patch.object(guard.urllib.request, "urlopen").start()
        self.sleep = patch.object(guard.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def error(self, status, retry_after=None):
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        return urllib.error.HTTPError(self.record["url"], status, "fixture", headers, io.BytesIO())

    def test_429_retry_after_is_capped_then_transient_503_succeeds(self):
        errors = [self.error(429, "999999"), self.error(503, "0")]
        self.network.side_effect = [*errors, io.BytesIO(self.data)]
        self.assertEqual(self.fetcher.url(self.record).read_bytes(), self.data)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [10.0, 2.0])
        self.assertTrue(all(error.closed for error in errors))
        self.assertEqual(self.network.call_count, 3)
        self.assertTrue(all(call.args[0].full_url == self.record["url"]
                            for call in self.network.call_args_list))
        self.assertEqual(self.record["sha256"], hashlib.sha256(self.data).hexdigest())

    def test_all_explicit_transient_statuses_retry(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.network.reset_mock()
                self.sleep.reset_mock()
                self.network.side_effect = [self.error(status), io.BytesIO(self.data)]
                value = guard.Fetcher(self.root / str(status)).url(self.record)
                self.assertEqual(value.read_bytes(), self.data)
                self.assertEqual(self.network.call_count, 2)
                self.sleep.assert_called_once_with(1.0)

    def test_nontransient_http_errors_are_not_retried_as_url_errors(self):
        for status in (400, 401, 403, 404, 501, 505):
            with self.subTest(status=status):
                self.network.reset_mock()
                self.sleep.reset_mock()
                self.network.side_effect = self.error(status)
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    guard.Fetcher(self.root / str(status)).url(self.record)
                self.assertEqual(self.network.call_count, 1)
                self.sleep.assert_not_called()

    def test_url_error_attempts_are_bounded_and_partial_cache_removed(self):
        self.network.side_effect = urllib.error.URLError("transport reset")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.fetcher.url(self.record)
        self.assertEqual(self.network.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1.0, 2.0, 4.0])
        self.assertEqual(list(self.fetcher.cache.iterdir()), [])

    def test_exhausted_429_closes_every_error_response(self):
        errors = [self.error(429, "999") for _ in range(4)]
        self.network.side_effect = errors
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.fetcher.url(self.record)
        self.assertEqual(self.network.call_count, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [10.0] * 3)
        self.assertTrue(all(error.closed for error in errors))

    def test_midstream_url_error_restarts_body_instead_of_appending(self):
        class Partial(io.BytesIO):
            def read(self, amount=-1):
                if getattr(self, "started", False):
                    raise urllib.error.URLError("midstream reset")
                self.started = True
                return b"partial"
        partial = Partial()
        self.network.side_effect = [partial, io.BytesIO(self.data)]
        self.assertEqual(self.fetcher.url(self.record).read_bytes(), self.data)
        self.assertTrue(partial.closed)
        self.assertEqual(self.network.call_count, 2)
        self.sleep.assert_called_once_with(1.0)

    def test_hash_size_and_oversize_mismatches_never_retry(self):
        for data in (b"x" * len(self.data), self.data[:-1], self.data + b"EXTRA"):
            with self.subTest(data=data):
                self.network.reset_mock()
                self.sleep.reset_mock()
                self.network.side_effect = None
                self.network.return_value = io.BytesIO(data)
                with self.assertRaisesRegex(ValueError, "size/SHA-256 differs"):
                    self.fetcher.url(self.record)
                self.assertEqual(self.network.call_count, 1)
                self.sleep.assert_not_called()
                self.assertEqual(list(self.fetcher.cache.iterdir()), [])

    def test_retry_after_dates_invalid_negative_and_past_values(self):
        with patch.object(guard.time, "time", return_value=1000000):
            self.assertEqual(guard.public_http_retry_delay(0, {"Retry-After": formatdate(1000600, usegmt=True)}), 10.0)
            for value in ("nonsense", "-999", "NaN", formatdate(999000, usegmt=True)):
                self.assertEqual(guard.public_http_retry_delay(1, {"Retry-After": value}), 2.0)

    def test_local_and_authentication_errors_are_not_retryable(self):
        for error in (OSError("local disk full"), ValueError("bad hash"), TimeoutError("not a URLError")):
            operation = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                guard.retry_public_http(operation)
            operation.assert_called_once()
        self.sleep.assert_not_called()

    def test_authenticated_cached_body_needs_no_network(self):
        self.network.return_value = io.BytesIO(self.data)
        first = self.fetcher.url(self.record)
        self.network.reset_mock()
        self.assertEqual(self.fetcher.url(self.record), first)
        self.network.assert_not_called()
        self.sleep.assert_not_called()

    def png(self):
        def chunk(name, data):
            return (guard.struct.pack(">I", len(data)) + name + data +
                    guard.struct.pack(">I", guard.zlib.crc32(name + data) & 0xffffffff))
        return (b"\x89PNG\r\n\x1a\n" +
                chunk(b"IHDR", guard.struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", guard.zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))

    def test_recorded_timestamp_png_reuses_http_retry_without_changing_bytes(self):
        raw = self.png()
        recipe = {"kind": "png-recorded-timestamps", "source": {
            "url": self.record["url"], "bytes": len(raw), "stable_sha256": guard.png_stable_sha(raw)},
            "recorded": []}
        self.network.side_effect = [self.error(429), io.BytesIO(raw)]
        self.assertEqual(self.fetcher.render(recipe), raw)
        self.assertEqual(self.network.call_count, 2)
        self.sleep.assert_called_once_with(1.0)

    def test_recorded_timestamp_png_authentication_mismatch_does_not_retry(self):
        raw = self.png()
        recipe = {"kind": "png-recorded-timestamps", "source": {
            "url": self.record["url"], "bytes": len(raw), "stable_sha256": "0" * 64},
            "recorded": []}
        self.network.return_value = io.BytesIO(raw)
        with self.assertRaisesRegex(ValueError, "non-timestamp bytes differ"):
            self.fetcher.render(recipe)
        self.assertEqual(self.network.call_count, 1)
        self.sleep.assert_not_called()

    def task(self, transport=None):
        task = self.root / "task"
        task.mkdir()
        entry = dict(self.record, name="data", destination="environment/data", scope="public")
        if transport:
            entry["transport"] = transport
            entry.pop("url")
        (task / "payload.json").write_text(json.dumps({"files": [entry]}))
        return task

    def test_direct_stage_reuses_retries_and_preserves_manifest_and_other_tasks(self):
        task = self.task()
        before = (task / "payload.json").read_bytes()
        other = self.root / "tasks/unavailable/provenance.json"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"unavailable; do not parse or rewrite")
        self.network.side_effect = [self.error(429, "1"), io.BytesIO(self.data)]
        with patch.object(staging, "ROOT", self.root):
            self.assertEqual(staging.stage(task, fetch=True), 1)
        self.assertEqual((task / "environment/data").read_bytes(), self.data)
        self.assertEqual((task / "payload.json").read_bytes(), before)
        self.assertEqual(other.read_bytes(), b"unavailable; do not parse or rewrite")
        self.assertEqual(self.network.call_count, 2)
        self.sleep.assert_called_once_with(1.0)

    def test_direct_stage_hash_mismatch_does_not_retry_or_publish(self):
        task = self.task()
        self.network.return_value = io.BytesIO(b"wrong")
        with patch.object(staging, "ROOT", self.root), self.assertRaisesRegex(ValueError, "SHA-256 differs"):
            staging.stage(task, fetch=True)
        self.assertFalse((task / "environment/data").exists())
        self.assertEqual(self.network.call_count, 1)
        self.sleep.assert_not_called()

    def test_transport_stage_authenticates_transport_and_final_bytes(self):
        png = b"pinned PNG fixture"
        transport = {"kind": "png-rgb24", "url": self.record["url"],
                     "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest()}
        task = self.task(transport)
        self.network.side_effect = [self.error(503), io.BytesIO(png)]
        def convert(argv, **kwargs):
            Path(argv[-1]).write_bytes(self.data)
        with patch.object(staging, "ROOT", self.root), patch.object(staging.subprocess, "run", side_effect=convert):
            self.assertEqual(staging.stage(task, fetch=True), 1)
        self.assertEqual((task / "environment/data").read_bytes(), self.data)
        self.assertEqual(self.network.call_count, 2)


class GuardRetryReleaseTests(unittest.TestCase):
    def test_new_release_preserves_old_registry_and_only_syncs_guard_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            environment = root / "tasks/example/environment"
            environment.mkdir(parents=True)
            new_guard = b"new retry guard bytes"
            (root / "tools/payload_check.py").write_bytes(new_guard)
            copy = environment / "payload_check.py"
            copy.write_bytes(b"old guard")
            provenance = environment.parent / "provenance.json"
            provenance.write_bytes(b"frozen provenance bytes")
            old = {"id": "frozen-old", "sha256": hashlib.sha256(b"old guard").hexdigest(),
                   "features": ["old-expectations"]}
            path = root / "tools/payload_guard_versions.json"
            path.write_text(json.dumps({"schema_version": 1, "current": old["id"], "versions": [old]}))
            frozen = path.read_bytes()
            with patch.object(releases, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "immutable"):
                    releases.release("frozen-old")
                self.assertEqual(path.read_bytes(), frozen)
                releases.release("new-retry-guard")
                releases.release("new-retry-guard")
                registry = json.loads(path.read_text())
                self.assertEqual(registry["versions"][0], old)
                self.assertEqual(len(registry["versions"]), 2)
                self.assertIn("bounded-public-http-retries", registry["versions"][-1]["features"])
                self.assertEqual(copy.read_bytes(), new_guard)
                self.assertEqual(provenance.read_bytes(), b"frozen provenance bytes")

    def test_previous_repo_lfs_v2_hash_remains_registered_and_unchanged(self):
        registry = json.loads((Path(guard.__file__).parent / "payload_guard_versions.json").read_text())
        old = next(row for row in registry["versions"] if row["id"] == "repo-lfs-public-v2")
        self.assertEqual(old["sha256"], "68aa1b3a84ed91a90a634e88b89efd48d521f6afa121c4b940336d095d8ec151")
        self.assertNotIn("revoked", old)


if __name__ == "__main__":
    unittest.main()
