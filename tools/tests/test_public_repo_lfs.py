"""Recovered public bytes are private-repo transports, not runtime downloads."""
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from payload_check import repo_lfs_spec, verified, acquire
from stage_payload import stage, missing


class PublicRepoLfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        self.raw = b"authenticated recovered bytes\0"
        self.record = {
            "kind": "repo-lfs", "scope": "public", "name": "input.bin",
            "source": "public/files/input.bin",
            "destination": "environment/workload/inputs/data/input.bin",
            "bytes": len(self.raw), "sha256": hashlib.sha256(self.raw).hexdigest(),
            "reason": "Recovered original prepared packet; no authenticated upstream recipe.",
        }
        self.source = self.task / self.record["source"]
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(self.raw)
        (self.task / "payload.json").write_text(json.dumps({"task": "test", "files": [self.record]}))

    def test_stage_and_public_only_build_guard(self):
        self.assertEqual(missing(self.task, "public", allow_fetch=True), [])
        self.assertEqual(stage(self.task, scope="public"), 1)
        target = self.task / self.record["destination"]
        self.assertEqual(target.read_bytes(), self.raw)
        environment = self.task / "environment"
        public_record = {**self.record, "destination": self.record["destination"][12:]}
        (environment / "payload-public.json").write_text(json.dumps({
            "task": "test", "files": [public_record]}))
        # The Docker build has only staged bytes, not the public LFS source.
        acquire(environment)
        self.assertTrue(verified(target, self.record))
        guard = Path(__file__).resolve().parents[1] / "payload_check.py"
        subprocess.run([sys.executable, str(guard), "--fetch", str(environment)],
                       check=True, capture_output=True)

    def test_reject_upstream_transport(self):
        for extra in ({"url": "https://example.com/original"}, {"fetch": {"url": "https://example.com/o"}},
                      {"transport": {"url": "https://example.com/o"}}):
            with self.assertRaisesRegex(ValueError, "upstream URL"):
                repo_lfs_spec({**self.record, **extra})

    def test_reject_hash_mismatch(self):
        self.source.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "size/SHA-256"):
            stage(self.task, scope="public")

    def test_pointer_fails_closed(self):
        self.source.write_bytes(b"version https://git-lfs.github.com/spec/v1\n"
                                b"oid sha256:" + b"a" * 64 + b"\nsize 2000000\n")
        with self.assertRaisesRegex(ValueError, r"git lfs pull"):
            stage(self.task, scope="public")

    def test_environment_source_forbidden(self):
        with self.assertRaisesRegex(ValueError, "never environment"):
            repo_lfs_spec({**self.record, "source": "environment/body"})

    def test_readme_explains_recovered_transport_without_linking_research_notes(self):
        from update_catalog import distribution, task_readmes
        (self.task / "task.json").write_text(json.dumps({"title": "Original task", "example_count": 1}))
        (self.task / "RESEARCH.md").write_text("Protocol notes")
        self.assertEqual(distribution(self.task)["operator"], 0)
        self.assertEqual(distribution(self.task)["repo-lfs"], len(self.raw))
        readme, _ = task_readmes(self.task, "heldout-missing")
        self.assertIn("git lfs pull --include=", readme)
        self.assertIn("Held-out LFS bodies are not needed for public builds", readme)
        # Working research notes are not part of the public Task README.
        self.assertNotIn("RESEARCH.md", readme)

    def test_unsafe_paths_reason_and_scope(self):
        for changes in ({"source": "public/files/../../environment/body"},
                        {"reason": ""}, {"scope": "heldout"}):
            with self.assertRaises(ValueError):
                repo_lfs_spec({**self.record, **changes})

    def test_catalog_can_count_unfetched_private_pointer_without_accepting_it(self):
        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1\n"
        path = self.task / "tests/heldout/files/private"
        path.parent.mkdir(parents=True)
        path.write_bytes(pointer)
        # Even authenticating a forged pointer's own hash must never accept it.
        entry = {"scope": "heldout", "name": "private",
                 "destination": "tests/heldout/files/private",
                 "bytes": len(pointer), "sha256": hashlib.sha256(pointer).hexdigest()}
        (self.task / "payload.json").write_text(json.dumps({"files": [entry]}))
        self.assertEqual(missing(self.task, "heldout", allow_unfetched_private=True), [entry])
        with self.assertRaisesRegex(ValueError, "git lfs pull"):
            missing(self.task, "heldout")
