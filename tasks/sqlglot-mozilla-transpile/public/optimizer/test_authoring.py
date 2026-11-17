"""Synthetic authoring regressions. No real private payload is used."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import mozilla_private
import prepare as public_author
import prepare_private as author
from prepare import dump, sha


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patched = patch.object(author, "TASK", self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.args = SimpleNamespace(
            freeze=True, replace_diagnostic=False, workdir=self.root / "work")
        self.original = self.descriptor("original", b"original input", b"original output", False)
        self.old = self.descriptor("diagnostic", b"old request", b"old output", True)
        self.manifest = {
            "format": "synthetic", "task": "fixture",
            "splits": {"private": [{"docs": [self.original, self.old]}]},
        }
        previous = {
            "format": "sqlglot-private-optimizer-v1",
            "source_archive": {"sha256": author.OLD_DIAGNOSTIC_ARCHIVE_SHA},
            "members": [{"request_sha256": self.old["sha256"]}],
        }
        bodies = {
            "heldout/files/" + self.original["sha256"]: b"original input",
            "heldout/expected/" + self.original["expect_sha"]: b"original output",
            "heldout/files/" + self.old["sha256"]: b"old request",
            "heldout/expected/optimizer/" + self.old["expect_sha"]: b"old output",
            "heldout/manifest.json": dump(self.manifest),
            "heldout/optimizer-provenance.json": dump(previous),
        }
        self.lock = {"files": {name: sha(raw) for name, raw in bodies.items()}}
        for relative, raw in bodies.items():
            self.write("tests/" + relative, raw)
        self.write("tests/heldout.lock.json", dump(self.lock))
        self.write("payload.json", dump({
            "files": [{"destination": "tests/" + name, "sha256": sha(raw), "bytes": len(raw)}
                      for name, raw in bodies.items()],
        }))
        self.cases = [(b"new request one", b"new output one"), (b"new request two", b"new output two")]
        self.members = [{"request_sha256": sha(raw)} for raw, _ in self.cases]
        self.provenance = {
            "format": "sqlglot-private-optimizer-v2",
            "source_archive": mozilla_private.ARCHIVE, "members": self.members,
        }

    @staticmethod
    def descriptor(identity, raw, output, optimizer):
        value = {
            "path": identity, "sha256": sha(raw), "bytes": len(raw),
            "expect_sha": sha(output), "expect_len": len(output),
        }
        if optimizer:
            value["family"] = "optimizer"
        return value

    def write(self, relative, raw):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def freeze(self):
        author.freeze(self.cases, self.members, self.provenance, self.args)

    def test_replacement_requires_explicit_authorization(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "commitments differ"):
            self.freeze()
        self.assertEqual(self.snapshot(), before)

    def test_replacement_preserves_original_and_verifies_without_writes(self):
        self.args.replace_diagnostic = True
        self.freeze()
        manifest = json.loads((self.root / "tests/heldout/manifest.json").read_text())
        docs = [d for batches in manifest["splits"].values() for b in batches for d in b["docs"]]
        self.assertEqual([d for d in docs if d.get("family") != "optimizer"], [self.original])
        self.assertEqual(len(docs), 3)
        self.assertEqual((self.root / "tests/heldout/files" / self.original["sha256"]).read_bytes(),
                         b"original input")
        self.assertFalse((self.root / "tests/heldout/files" / self.old["sha256"]).exists())
        self.args.freeze = False
        before = self.snapshot()
        self.freeze()
        self.assertEqual(self.snapshot(), before)

    def test_adopted_collection_cannot_be_replaced(self):
        self.args.replace_diagnostic = True
        self.freeze()
        before = self.snapshot()
        self.cases = [(b"changed", b"changed")]
        with self.assertRaisesRegex(ValueError, "commitments differ"):
            self.freeze()
        self.assertEqual(self.snapshot(), before)

    def test_changed_existing_body_fails_before_publication(self):
        self.args.replace_diagnostic = True
        self.write("tests/heldout/files/" + sha(self.cases[0][0]), b"corrupted")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "artifact differs"):
            self.freeze()
        self.assertEqual(self.snapshot(), before)

    def test_nested_publisher_types_and_unknown_rejection(self):
        self.assertEqual(mozilla_private.dtype({
            "type": "RECORD", "mode": "REPEATED",
            "fields": [{"name": "active", "type": "BOOLEAN"}, {"name": "amount", "type": "FLOAT"}],
        }), "ARRAY<STRUCT<`active` BOOL, `amount` FLOAT64>>")
        with self.assertRaises(KeyError):
            mozilla_private.dtype({"type": "invented"})

    def test_failures_do_not_disclose_private_diagnostics(self):
        work = self.root / "failure-work"
        argv = ["prepare_private.py", "--runtime", "/unused", "--archive", "/unused",
                "--workdir", str(work)]
        output = io.StringIO()
        errors = io.StringIO()
        with patch("sys.argv", argv), patch.object(author, "baseline", side_effect=ValueError("PRIVATE_SENTINEL")):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
                author.main()
        self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(json.loads(output.getvalue())["error_class"], "ValueError")
        self.assertIn("PRIVATE_SENTINEL", (work / "private-authoring-details.log").read_text())


class PublicAuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cases = [("one", "first-source", {"sql": "SELECT 1", "schema": {}, "dialect": None})]
        self.source_records = {"fixture": {"sha256": "original-source"}}
        self.original = {"path": "legacy", "sha256": "original-input"}
        initial = {
            "environment/workload/inputs/manifest.json": {
                "splits": {"train": [{"docs": [self.original]}]}},
            "payload.json": {"schema_version": 1, "task": "fixture", "files": []},
            "environment/payload-public.json": {"schema_version": 1, "task": "fixture", "files": []},
        }
        for relative, value in initial.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(dump(value))
        self.run_author()
        self.cases.append(("two", "second-source", {"sql": "SELECT 2", "schema": {}, "dialect": None}))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix():
                (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
                for p in self.root.rglob("*")}

    def run_author(self, extend=False, freeze=True):
        optimizer = SimpleNamespace(
            encode=dump,
            run_case=lambda request: dump({"status": "ok", "logs": [], "python_warnings": []}))
        argv = ["prepare.py", "--runtime", "/unused"]
        if freeze:
            argv.append("--freeze")
        if extend:
            argv.append("--extend")
        with (patch.object(public_author, "TASK", self.root),
              patch.object(public_author, "baseline", return_value=optimizer),
              patch.object(public_author, "sources",
                           return_value=({}, {"LICENSE": "synthetic license"}, self.source_records)),
              patch.object(public_author, "public_cases", return_value=self.cases),
              patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO())):
            public_author.main()

    def test_extension_without_authorization_has_no_writes(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "commitments differ"):
            self.run_author()
        self.assertEqual(self.snapshot(), before)

    def test_changed_source_provenance_has_no_writes(self):
        self.source_records["fixture"]["sha256"] = "changed-source"
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "altered existing provenance"):
            self.run_author(extend=True)
        self.assertEqual(self.snapshot(), before)

    def test_extension_preserves_prefix_and_verifies_without_writes(self):
        path = self.root / "environment/workload/inputs/manifest.json"
        original_manifest = json.loads(path.read_text())
        self.run_author(extend=True)
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["splits"]["train"][:2], original_manifest["splits"]["train"])
        self.assertEqual(len(manifest["splits"]["train"]), 3)
        before = self.snapshot()
        self.run_author(freeze=False)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
