"""Offline Git object fixtures only; the public HTTPS transport is injected."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import payload_check as payload
import stage_payload as staging
import update_catalog


@unittest.skipUnless(shutil.which("git") or os.environ.get("OPTIMBENCH_GIT"), "Git executable required for offline fixtures")
class GitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Offline Fixture")
        self.git("config", "user.email", "offline@example.invalid")
        (self.source / ".gitattributes").write_bytes(b"*.txt -text filter=forbidden\n")
        (self.source / "data.py").write_bytes(b"../tests/heldout is literal input, not package glue\n")
        (self.source / "folder").mkdir()
        (self.source / "folder/data.txt").write_bytes(b"first\r\n\x00\xff")
        (self.source / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
        (self.source / "run.sh").chmod(0o755)
        (self.source / "link").symlink_to("folder/data.txt")
        self.git("add", ".")
        self.git("commit", "-qm", "original message\n\nUntouched history.")
        self.parent = self.git("rev-parse", "HEAD").strip().decode()
        (self.source / "folder/data.txt").write_bytes(b"second\r\n\x00\xff")
        self.git("add", ".")
        self.git("commit", "-qm", "second original commit")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        # A fetch is allowed to deliver extra objects; publication must remove them.
        self.extra = self.git("hash-object", "-w", "--stdin", input=b"unreachable server object").strip()
        self.actual_fetch = payload._git_fetch
        self.fetch = patch.object(payload, "_git_fetch", side_effect=self.transport)
        self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.canonical = self.root / "canonical"
        inventories = payload.materialize_git_history(
            "https://example.com/history.git", self.commit, self.canonical)
        self.record = {"kind": "git-history", "scope": "public", "name": "history",
                       "repository": "https://example.com/history.git", "commit": self.commit,
                       "ref_policy": "one-detached-commit-closure",
                       "destination": "environment/workload/projects/history", **inventories}

    def git(self, *args, input=None):
        result = subprocess.run(payload._git_args(self.source / ".git", *args),
                                cwd=self.source, input=input,
                                capture_output=True, env={**payload._git_env(),
                                    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
                                    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000"})
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def transport(self, gitdir, url, commit):
        self.assertEqual(url, "https://example.com/history.git")
        self.assertEqual(commit, self.commit)
        shutil.copytree(self.source / ".git/objects", gitdir / "objects", dirs_exist_ok=True)

    def test_fixture_git_uses_production_maintenance_and_gc_controls(self):
        # Even hostile local config must not start a background writer that
        # races the fixture transport's objects-directory copy.
        self.git("config", "maintenance.auto", "true")
        self.git("config", "gc.auto", "1")
        with patch.object(payload, "_git_args", wraps=payload._git_args) as command:
            self.git("commit", "--allow-empty", "-qm", "fixture without maintenance")
        command.assert_called_once_with(
            self.source / ".git", "commit", "--allow-empty", "-qm", "fixture without maintenance")
        argv = payload._git_args(self.source / ".git", "status", "--porcelain")
        self.assertIn("maintenance.auto=false", argv)
        self.assertIn("gc.auto=0", argv)
        self.assertFalse((self.source / ".git/objects/maintenance.lock").exists())

    def test_private_git_reference_acquires_and_authenticates_outside_worktree(self):
        from judge import heldout
        task = self.root / "private-task"
        tests = task / "tests"
        (tests / "heldout/files").mkdir(parents=True)
        retained = b"retained fixture"
        digest = hashlib.sha256(retained).hexdigest()
        (tests / "heldout/files" / digest).write_bytes(retained)
        manifest = tests / "heldout/manifest.json"
        manifest.write_text(json.dumps({"splits": {"private": [{"docs": [{
            "sha256": digest, "expect_sha": digest, "expect_len": len(retained)}]}]}}))
        entry = {key: value for key, value in self.record.items()
                 if key not in ("destination", "name")}
        entry.update(scope="heldout", role="input", license_basis="fetch-only; no redistribution")
        name = "heldout/projects/history"
        lock = {"schema_version": 1, "status": "available", "archives": [],
                "files": {"heldout/manifest.json": heldout.sha(manifest),
                          "heldout/files/" + digest: digest},
                "references": {name: entry}}
        (tests / "heldout.lock.json").write_text(json.dumps(lock))
        (task / "payload.json").write_text(json.dumps({"files": [{
            **entry, "name": "history", "destination": "tests/" + name,
            "retention": "fetch-only"}]}))
        self.assertEqual(len(staging.missing(task, "heldout")), 1)
        with patch.object(heldout.git_tools(), "_git_fetch", side_effect=self.transport):
            with heldout.materialized_task(task) as view:
                history = view / "tests" / name
                self.assertTrue(heldout.reference_matches(history, entry))
                self.assertEqual((history / "folder/data.txt").read_bytes(), b"second\r\n\x00\xff")
                heldout.check(view / "tests", require_available=True)
                (history / "folder/data.txt").write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    heldout.check(view / "tests", require_available=True)
            self.assertFalse(view.exists())
        self.assertFalse((tests / name).exists())

    def clone_payload(self, name="changed"):
        target = self.root / name
        shutil.copytree(self.canonical, target, symlinks=True)
        return target

    def test_positive_raw_framing_and_original_checkout(self):
        self.assertTrue(payload.verified(self.canonical, self.record))
        self.assertEqual((self.canonical / "folder/data.txt").read_bytes(), b"second\r\n\x00\xff")
        self.assertEqual(os.readlink(self.canonical / "link"), "folder/data.txt")
        ids = sorted(set(self.git("rev-list", "--objects", "--no-object-names", self.commit).splitlines()))
        framed = bytearray()
        for oid in ids:
            kind = self.git("cat-file", "-t", oid.decode()).strip()
            raw = self.git("cat-file", kind.decode(), oid.decode())
            for value in (oid, kind, raw):
                framed += struct.pack(">Q", len(value)) + value
        self.assertEqual(self.record["object_inventory"], {
            "algorithm": "sha256-canonical-git-objects-v1", "object_count": len(ids),
            "bytes": len(framed), "sha256": hashlib.sha256(framed).hexdigest()})
        self.assertNotIn(self.extra, payload._git(self.canonical / ".git", "cat-file",
                         "--batch-all-objects", "--batch-check=%(objectname)").splitlines())
        self.assertIn(self.parent.encode(), ids)
        self.assertEqual(payload._git(self.canonical / ".git", "for-each-ref"), b"")
        self.assertEqual(payload._git(self.canonical / ".git", "log", "--format=%H%n%B", self.commit),
                         self.git("log", "--format=%H%n%B", self.commit))
        status = subprocess.run([payload._git_binary(), "-C", str(self.canonical), "status", "--porcelain"],
                                capture_output=True, env=payload._git_env())
        self.assertEqual((status.returncode, status.stdout), (0, b""))

    def test_pack_layout_is_not_the_integrity_contract(self):
        target = self.clone_payload()
        oid = self.git("rev-parse", self.commit + ":folder/data.txt").strip().decode()
        raw = self.git("cat-file", "blob", oid)
        loose = target / ".git/objects" / oid[:2] / oid[2:]
        loose.parent.mkdir(exist_ok=True)
        loose.write_bytes(zlib.compress(b"blob " + str(len(raw)).encode() + b"\0" + raw, level=1))
        self.assertTrue(payload.verified(target, self.record))

    def test_staging_fetch_public_guard_and_distribution(self):
        task = self.root / "task"
        task.mkdir()
        (task / "payload.json").write_text(json.dumps({"files": [self.record]}))
        self.assertEqual(len(staging.missing(task, "public")), 1)
        self.assertEqual(staging.missing(task, "public", allow_fetch=True), [])
        self.assertEqual(staging.stage(task, fetch=True, scope="public"), 1)
        self.assertEqual(staging.missing(task), [])
        environment = task / "environment"
        public_record = {**self.record, "destination": self.record["destination"].removeprefix("environment/")}
        (environment / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [public_record]}))
        payload.check(environment)
        payload.acquire(environment)
        # Independent directory copy from a packet is also authenticated.
        packet = self.root / "packet"
        destination = packet / self.record["destination"]
        destination.parent.mkdir(parents=True)
        shutil.copytree(task / self.record["destination"], destination, symlinks=True)
        shutil.rmtree(task / self.record["destination"])
        self.assertEqual(staging.stage(task, source=packet, scope="public"), 1)
        record = {**self.record, "distribution": "upstream"}
        (task / "payload.json").write_text(json.dumps({"files": [record]}))
        distribution = update_catalog.distribution(task)
        self.assertEqual(distribution["git"], 0)
        self.assertEqual(distribution["upstream"], self.record["object_inventory"]["bytes"])

    def cached_public_history(self):
        task = self.root / "tasks/git-history-fixture"
        task.mkdir(parents=True)
        record = {**self.record, "name": "git-release-history",
                  "destination": "environment/workload/inputs/histories/git"}
        (task / "payload.json").write_text(json.dumps({"files": [record]}))
        workload = self.root / "public-workload"
        history = workload / "inputs/histories/git"
        history.parent.mkdir(parents=True)
        shutil.copytree(self.canonical, history, symlinks=True)
        return task, workload, history, record

    def test_staging_git_history_from_cached_public_workload(self):
        from judge.public_input_preflight import ensure_public_inputs
        task, workload, history, record = self.cached_public_history()
        self.assertEqual(staging.stage(task, source=workload, scope="public"), 1)
        destination = task / record["destination"]
        self.assertTrue(payload.verified(destination, record))
        self.assertTrue(payload.verified(history, record))
        shutil.rmtree(destination)
        receipt = ensure_public_inputs(task, source=workload, root=self.root)
        self.assertEqual(receipt["cache_restored_entries"], 1)
        self.assertTrue(payload.verified(destination, record))

    def test_cached_public_history_missing_or_tampered_still_fails_closed(self):
        from judge.public_input_preflight import PublicInputsError, ensure_public_inputs
        task, workload, history, record = self.cached_public_history()
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    shutil.rmtree(history)
                else:
                    (history / "folder/data.txt").write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    staging.stage(task, source=workload, scope="public")
                with self.assertRaisesRegex(PublicInputsError, "Public cache restoration failed"):
                    ensure_public_inputs(task, source=workload, root=self.root)
                self.assertFalse((task / record["destination"]).exists())

    def test_check_tasks_accepts_declared_history_data_and_rejects_retention(self):
        import check_tasks
        repository = self.root / "package"
        subprocess.run([payload._git_binary(), "init", "-q", str(repository)], check=True, env=payload._git_env())
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        task = repository / "tasks/git-history-fixture"
        # A complete synthetic shell: never copy a real task's private payloads,
        # including intentionally unfetched pointers in no-private-payload CI.
        for name in check_tasks.REQUIRED:
            path = task / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}" if path.suffix == ".json" else "offline fixture\n")
        (task / "task.json").write_text(json.dumps({"id": task.name, "example_count": 1}))
        (task / "task.toml").write_text(
            'schema_version = "1.4"\n[agent]\ntimeout_sec = 7200\n'
            '[environment]\ncpus = 8\n[verifier]\nenvironment_mode = "separate"\n'
            '[metadata.optimbench]\n'
            f'baseline_git_tree = "{tree}"\nbaseline_git_commit = "{self.commit}"\n'
            'baseline_git_date = "2000-01-01T00:00:00+0000"\n')
        (task / "environment/Dockerfile").write_text(
            "FROM scratch\n# Synthetic shell; this test validates layout, not image execution.\n"
            "COPY payload_check.py /payload_check.py\n"
            "RUN git -c init.defaultBranch=optimbench-baseline init && "
            f'test "$(git rev-parse HEAD^{{tree}})" = {tree} && '
            f'test "$(git rev-parse HEAD)" = {self.commit}\n')
        (task / "environment/workload/task.py").write_text(
            "ENV = {}\ndef cases(inputs): return []\ndef prepare(case, ctx): return []\n"
            "def collect(case, ctx, results): return {}\n")
        shutil.copyfile(TOOLS / "runner/runner.py", task / "environment/workload/runner.py")
        (task / "tests/Dockerfile").write_text(
            "FROM scratch\nCOPY judge/requirements-interface.lock /opt/verifier/vendor/requirements-interface.lock\n")
        (task / "tests/judge.json").write_text(json.dumps({"public_interface": {"kind": "binary"}}))
        private_manifest = task / "tests/heldout/manifest.json"
        private_manifest.write_text('{"splits":{}}')
        (task / "tests/heldout.lock.json").write_text(json.dumps({
            "schema_version": 1, "status": "unavailable", "archives": [],
            "files": {"heldout/manifest.json": hashlib.sha256(private_manifest.read_bytes()).hexdigest()}}))
        (task / "qualification-status.json").write_text(json.dumps({"task": task.name}))
        (task / "payload.json").write_text(json.dumps({"schema_version": 1, "task": task.name, "files": []}))
        (repository / "tools/runner").mkdir(parents=True)
        shutil.copyfile(TOOLS / "runner/runner.py", repository / "tools/runner/runner.py")
        shutil.copyfile(TOOLS / "payload_check.py", repository / "tools/payload_check.py")
        shutil.copyfile(TOOLS / "payload_guard_versions.json", repository / "tools/payload_guard_versions.json")
        shutil.copyfile(TOOLS / "payload_check.py", task / "environment/payload_check.py")
        (repository / "README.md").write_text(update_catalog.START + "\n" + update_catalog.END + "\n")
        (repository / "datasets").mkdir()
        record = {**self.record, "distribution": "upstream"}
        manifest = json.loads((task / "payload.json").read_text())
        manifest["files"].append(record)
        (task / "payload.json").write_text(json.dumps(manifest))
        public = {"schema_version": 1, "task": task.name, "files": [
            {**entry, "destination": entry["destination"].removeprefix("environment/")}
            for entry in manifest["files"] if entry["scope"] == "public"]}
        (task / "environment/payload-public.json").write_text(json.dumps(public))
        target = task / record["destination"]
        target.parent.mkdir(parents=True)
        shutil.copytree(self.canonical, target, symlinks=True)
        with patch.object(update_catalog, "ROOT", repository), patch.object(check_tasks, "ROOT", repository):
            # Settle freshly generated shell text before binding its hashes.
            for _ in range(3):
                for path, text in update_catalog.generated().items():
                    path.write_text(text)
            self.assertTrue(check_tasks.check_task(task)["shell_complete"])
            bindings = json.loads((task / "provenance.json").read_text())["current_files_sha256"]
            self.assertFalse(any(name.startswith(record["destination"] + "/") for name in bindings))
            retained = (target / "data.py").relative_to(repository).as_posix()
            with patch.object(check_tasks.subprocess, "check_output", return_value=(retained + "\0").encode()):
                with self.assertRaisesRegex(AssertionError, "fetch-only"):
                    check_tasks.check_task(task)
            (target.parent / "undeclared").mkdir()
            with self.assertRaisesRegex(AssertionError, "Undeclared"):
                check_tasks.check_task(task)

    def test_reject_each_extra_semantic_state(self):
        changes = {
            "maintenance-lock": ("objects/maintenance.lock", b"unexpected lock"),
            "shallow": ("shallow", self.parent.encode() + b"\n"),
            "promisor": ("objects/pack/extra.promisor", b""),
            "partial-config": ("config", (self.canonical / ".git/config").read_bytes() +
                               b'[remote "origin"]\n\tpromisor = true\n\tpartialclonefilter = blob:none\n'),
            "replace": ("refs/replace/" + self.parent, self.commit.encode()),
            "grafts": ("info/grafts", self.commit.encode()),
            "alternates": ("objects/info/alternates", b"/outside/objects\n"),
            "http-alternates": ("objects/info/http-alternates", b"https://example.com/objects\n"),
            "branch": ("refs/heads/extra", self.commit.encode()),
            "tag": ("refs/tags/extra", self.commit.encode()),
            "attributes": ("info/attributes", b"* filter=evil\n"),
            "hooks": ("hooks/post-checkout", b"#!/bin/sh\nexit 1\n"),
            "reflog": ("logs/HEAD", b"unexpected"),
            "sparse": ("info/sparse-checkout", b"/*\n"),
            "worktree": ("commondir", b"/outside\n"),
            "symbolic-HEAD": ("HEAD", b"ref: refs/heads/main\n"),
            "noncanonical-index": ("index", b"not an index"),
        }
        for name, (relative, raw) in changes.items():
            with self.subTest(name=name):
                target = self.clone_payload(name)
                path = target / ".git" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                with self.assertRaises((ValueError, OSError)):
                    payload.verified(target, self.record)

    def test_reject_altered_blob_tree_and_commit_objects(self):
        oids = [self.commit, self.git("rev-parse", self.commit + "^{tree}").strip().decode(),
                self.git("rev-parse", self.commit + ":folder/data.txt").strip().decode()]
        for index, oid in enumerate(oids):
            with self.subTest(oid=oid):
                target = self.clone_payload(str(index))
                kind = self.git("cat-file", "-t", oid).strip()
                raw = self.git("cat-file", kind.decode(), oid)
                path = target / ".git/objects" / oid[:2] / oid[2:]
                path.parent.mkdir(exist_ok=True)
                changed = raw + b"altered"
                path.write_bytes(zlib.compress(kind + b" " + str(len(changed)).encode() + b"\0" + changed))
                with self.assertRaises(ValueError):
                    payload.verified(target, self.record)

    def test_reject_missing_and_extra_objects(self):
        target = self.clone_payload("missing")
        next((target / ".git/objects/pack").glob("*.pack")).unlink()
        with self.assertRaises(ValueError):
            payload.verified(target, self.record)
        target = self.clone_payload("extra")
        oid = self.extra.decode()
        path = target / ".git/objects" / oid[:2] / oid[2:]
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(self.source / ".git/objects" / oid[:2] / oid[2:], path)
        with self.assertRaisesRegex(ValueError, "extra or missing"):
            payload.verified(target, self.record)

    def test_reject_checkout_changes_even_if_checkout_digest_is_replaced(self):
        for name in ("bytes", "mode", "attributes", "extra", "directory", "symlink"):
            with self.subTest(name=name):
                target = self.clone_payload(name)
                if name == "bytes":
                    (target / "folder/data.txt").write_bytes(b"changed")
                elif name == "mode":
                    (target / "run.sh").chmod(0o644)
                elif name == "attributes":
                    (target / ".gitattributes").write_text("* text\n")
                elif name == "extra":
                    (target / "extra").write_bytes(b"extra")
                elif name == "directory":
                    (target / "extra").mkdir()
                else:
                    (target / "link").unlink()
                    (target / "link").symlink_to("/outside")
                with self.assertRaises(ValueError):
                    payload.verified(target, self.record)

    def test_reject_wrong_inventory_counts_bytes_and_digests(self):
        for field in ("object_count", "bytes", "sha256", "checkout_inventory_sha256"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                if field == "checkout_inventory_sha256":
                    record[field] = "f" * 64
                elif field == "sha256":
                    record["object_inventory"][field] = "f" * 64
                else:
                    record["object_inventory"][field] += 1
                with self.assertRaises(ValueError):
                    payload.verified(self.canonical, record)

    def test_reject_unpinned_or_malformed_inventory_schema(self):
        for field, value in (("object_count", True), ("bytes", "measured"), ("bytes", 0),
                             ("algorithm", "sha256-of-pack"), ("sha256", "not measured")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                record = copy.deepcopy(self.record)
                record["object_inventory"][field] = value
                payload.git_history_spec(record)

    def test_reject_git_metadata_symlinks_and_shared_objects(self):
        target = self.clone_payload("metadata-link")
        config = target / ".git/config"
        config.unlink()
        config.symlink_to(self.canonical / ".git/config")
        with self.assertRaises(ValueError):
            payload.verified(target, self.record)
        target = self.clone_payload("shared-pack")
        pack = next((target / ".git/objects/pack").glob("*.pack"))
        os.link(pack, self.root / "external-hardlink")
        with self.assertRaises(ValueError):
            payload.verified(target, self.record)

    def test_reject_malformed_warning_and_gitlink_pins(self):
        for warnings in [None, {"count": True, "sha256": "0" * 64},
                         {"count": -1, "sha256": "0" * 64},
                         {"count": 0, "sha256": "invalid"},
                         {"count": 0, "sha256": "0" * 64, "ignore": ["badFilemode"]}]:
            with self.subTest(warnings=warnings), self.assertRaises(ValueError):
                payload.git_history_spec({**self.record, "fsck_warnings": warnings})
        for gitlinks in [[], {"../escape": "f" * 40}, {"/absolute": "f" * 40},
                         {".git": "f" * 40}, {"submodule": "HEAD"}]:
            with self.subTest(gitlinks=gitlinks), self.assertRaises(ValueError):
                payload.git_history_spec({**self.record, "gitlinks": gitlinks})
    def test_reject_noncommit_pin_and_overlapping_declarations(self):
        tree = self.git("rev-parse", self.commit + "^{tree}").strip().decode()
        def transport(gitdir, url, commit):
            shutil.copytree(self.source / ".git/objects", gitdir / "objects", dirs_exist_ok=True)
        with patch.object(payload, "_git_fetch", side_effect=transport):
            with self.assertRaises(ValueError):
                payload.materialize_git_history("https://example.com/history.git", tree,
                                                self.root / "tree-pin")
        task = self.root / "declarations"
        task.mkdir()
        manifest = {"files": [self.record, {"scope": "public", "kind": "file",
                    "destination": self.record["destination"] + "/inside"}]}
        with self.assertRaises(ValueError):
            staging.git_history_roots(task, manifest)
        with self.assertRaises(ValueError):
            staging.git_history_roots(task, {"files": [{**self.record, "destination": "tests/history"}]})

    def test_actual_fetch_command_is_https_only_and_hermetic(self):
        with patch.object(payload.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.actual_fetch(self.canonical / ".git", self.record["repository"], self.commit)
        argv = run.call_args.args[0]
        self.assertIn("--no-tags", argv)
        self.assertIn("--no-write-fetch-head", argv)
        self.assertIn("--refmap=", argv)
        self.assertIn("http.followRedirects=false", argv)
        self.assertEqual(run.call_args.kwargs["env"]["GIT_ALLOW_PROTOCOL"], "https")

    def test_public_only_git_guard_main_check_fetch_and_receipt(self):
        environment = self.root / "public-only-cli"
        environment.mkdir()
        record = {**self.record, "destination": "history"}
        (environment / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [record]}))
        shutil.copytree(self.canonical, environment / "history", symlinks=True)
        for fetch in (False, True):
            command = [sys.executable, str(TOOLS / "payload_check.py"),
                       *(["--fetch"] if fetch else []), str(environment)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            receipt = json.loads(run.stdout)
            self.assertEqual(receipt["status"], "ready")
            self.assertEqual(set(receipt["git_tool"]), {"path", "version", "sha256"})
            self.assertTrue(receipt["git_tool"]["version"].startswith("git version "))
            self.assertFalse((environment / "payload.json").exists())

    def test_public_only_git_guard_cli_fetches_missing_fixture(self):
        environment = self.root / "public-only-missing-cli"
        environment.mkdir()
        record = {**self.record, "destination": "history"}
        (environment / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [record]}))
        # Only the operator-supplied Git transport is redirected to an offline
        # source. The standalone guard, object publication, and checks are real.
        wrapper = self.root / "fixture-git"
        real = payload._git_binary()
        wrapper.write_text(
            "#!" + sys.executable + "\nimport os, sys\nargs = sys.argv[1:]\n"
            "if 'fetch' in args:\n"
            "    args = [" + repr(str(self.source)) + " if a == " +
            repr(self.record["repository"]) + " else a for a in args]\n"
            "    os.environ['GIT_ALLOW_PROTOCOL'] = 'file'\n"
            "os.execv(" + repr(real) + ", [" + repr(real) + ", *args])\n")
        wrapper.chmod(0o755)
        env = {**os.environ, "OPTIMBENCH_GIT": str(wrapper)}
        for args in (["--fetch"], []):
            run = subprocess.run([sys.executable, str(TOOLS / "payload_check.py"), *args, str(environment)],
                                 capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "ready")
        self.assertEqual((environment / "history/folder/data.txt").read_bytes(), b"second\r\n\x00\xff")
        self.assertFalse((environment / "payload.json").exists())

    def test_git_outside_system_path_and_explicit_override_are_hermetic(self):
        real = payload._git_binary()
        directory = self.root / "nix-style-bin"
        directory.mkdir()
        wrapper = directory / "git"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(real) + ' "$@"\n')
        wrapper.chmod(0o755)
        empty = self.root / "empty-system-path"
        empty.mkdir()
        for override in (False, True):
            with self.subTest(override=override):
                environment = {"PATH": str(empty if override else directory),
                               "GIT_CONFIG_COUNT": "99", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/forbidden"}
                if override:
                    environment["OPTIMBENCH_GIT"] = str(wrapper)
                with patch.dict(os.environ, environment, clear=True), patch.object(os, "defpath", str(empty)):
                    payload._resolve_git.cache_clear()
                    try:
                        receipt = {}
                        result = payload.materialize_git_history(
                            self.record["repository"], self.commit, self.root / ("portable-" + str(override)),
                            receipt=receipt)
                        self.assertEqual(result["object_inventory"], self.record["object_inventory"])
                        self.assertEqual(receipt["git_tool"]["path"], str(wrapper))
                        self.assertEqual(receipt["git_tool"]["sha256"], hashlib.sha256(wrapper.read_bytes()).hexdigest())
                        self.assertTrue(receipt["git_tool"]["version"].startswith("git version "))
                        self.assertEqual(payload._git_args(self.canonical / ".git", "status")[0], str(wrapper))
                        self.assertEqual(payload._git_env()["PATH"], str(empty))
                        self.assertEqual(payload._git_env()["GIT_CONFIG_COUNT"], "0")
                        self.assertNotIn("GIT_ALTERNATE_OBJECT_DIRECTORIES", payload._git_env())
                        self.assertNotIn("OPTIMBENCH_GIT", payload._git_env())
                    finally:
                        payload._resolve_git.cache_clear()

    def test_invalid_git_override_fails_closed(self):
        for value in ("relative/git", str(self.root / "absent-git"), str(self.root)):
            with self.subTest(value=value), patch.dict(os.environ, {"OPTIMBENCH_GIT": value}):
                payload._resolve_git.cache_clear()
                try:
                    with self.assertRaises(ValueError):
                        payload.git_tool_receipt()
                finally:
                    payload._resolve_git.cache_clear()

    def test_sha256_repository_format(self):
        self.source = self.root / "sha256-source"
        self.source.mkdir()
        result = subprocess.run([payload._git_binary(), "init", "-q", "--object-format=sha256", str(self.source)],
                                capture_output=True, env=payload._git_env())
        if result.returncode:
            self.skipTest("Git build lacks SHA-256 repository support")
        self.git("config", "user.name", "Offline")
        self.git("config", "user.email", "offline@example.invalid")
        (self.source / "data").write_bytes(b"original sha256 repository bytes")
        self.git("add", ".")
        self.git("commit", "-qm", "original")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        target = self.root / "sha256-payload"
        result = payload.materialize_git_history(self.record["repository"], self.commit, target)
        record = {**self.record, **result, "commit": self.commit}
        self.assertEqual(len(self.commit), 64)
        self.assertTrue(payload.verified(target, record))

    def test_gitlinks_are_pinned_empty_and_never_initialized(self):
        foreign = "f" * 40
        (self.source / ".gitmodules").write_text(
            '[submodule "submodule"]\npath = submodule\nurl = file:///FORBIDDEN-NETWORK\n')
        self.git("add", ".gitmodules")
        self.git("update-index", "--add", "--cacheinfo", "160000," + foreign + ",submodule")
        self.git("commit", "-qm", "gitlink")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        target = self.root / "gitlink-payload"
        result = payload.materialize_git_history(self.record["repository"], self.commit, target)
        self.assertEqual(result["gitlinks"], {"submodule": foreign})
        record = {**self.record, **result, "commit": self.commit}
        self.assertTrue(payload.verified(target, record))
        self.assertTrue((target / "submodule").is_dir())
        self.assertEqual(list((target / "submodule").iterdir()), [])
        self.assertFalse((target / ".git/modules").exists())
        (target / "submodule").chmod(0o700)
        with self.assertRaises(ValueError):
            payload.verified(target, record)
        (target / "submodule").chmod(0o755)
        without_pin = {k: v for k, v in record.items() if k != "gitlinks"}
        with self.assertRaises(ValueError):
            payload.verified(target, without_pin)
        (target / "submodule/.git").write_text("gitdir: /outside\n")
        with self.assertRaises(ValueError):
            payload.verified(target, record)

    def test_original_unsafe_symlinks_are_not_materialized(self):
        (self.source / "link").unlink()
        (self.source / "link").symlink_to("/outside")
        self.git("add", "link")
        self.git("commit", "-qm", "unsafe link")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        with self.assertRaisesRegex(ValueError, "Unsafe checkout symlink"):
            payload.materialize_git_history(self.record["repository"], self.commit,
                                            self.root / "symlink-payload")

    def test_original_git_attribute_checkout_semantics_not_raw_blob_substitutes(self):
        (self.source / ".gitattributes").write_text("*.bat text eol=crlf\n")
        (self.source / "original.bat").write_bytes(b"original\nbytes\n")
        self.git("add", ".")
        self.git("commit", "-qm", "original CRLF checkout attributes")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        target = self.root / "attribute-checkout"
        result = payload.materialize_git_history(self.record["repository"], self.commit, target)
        record = {**self.record, **result, "commit": self.commit}
        self.assertEqual((target / "original.bat").read_bytes(), b"original\r\nbytes\r\n")
        self.assertEqual(self.git("show", self.commit + ":original.bat"), b"original\nbytes\n")
        self.assertTrue(payload.verified(target, record))
        (target / "original.bat").write_bytes(b"original\nbytes\n")
        forged = {**record, "checkout_inventory_sha256": payload._checkout_inventory(
            target, payload._git_tree(target / ".git", self.commit), self.commit)}
        with self.assertRaisesRegex(ValueError, "original attribute/checkout"):
            payload.verified(target, forged)
        (target / "original.bat").write_bytes(b"original\r\nbytes\r\n")
        (target / ".gitattributes").write_text("*.bat text eol=lf\n")
        with self.assertRaises(ValueError):
            payload.verified(target, record)

    def test_historic_fsck_warnings_require_exact_pinned_lines_not_categories(self):
        blob = self.git("hash-object", "-w", "--stdin", input=b"historic original bytes").strip()
        raw_tree = b"100664 data\0" + bytes.fromhex(blob.decode())
        tree = self.git("hash-object", "--literally", "-w", "-t", "tree", "--stdin", input=raw_tree).strip()
        historic = self.git("commit-tree", tree.decode(), "-m", "historic mode").strip().decode()
        clean_tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        self.commit = self.git("commit-tree", clean_tree, "-p", historic,
                               "-m", "current clean checkout, original historic warnings").strip().decode()
        target = self.root / "historic-mode"
        result = payload.materialize_git_history(self.record["repository"], self.commit, target)
        self.assertGreater(result["fsck_warnings"]["count"], 0)
        record = {**self.record, **result, "commit": self.commit}
        self.assertTrue(payload.verified(target, record))
        unapproved = {k: v for k, v in record.items() if k != "fsck_warnings"}
        with self.assertRaisesRegex(ValueError, "warning count/SHA-256"):
            payload.verified(target, unapproved)
        for bad in ({**result["fsck_warnings"], "count": result["fsck_warnings"]["count"] + 1},
                    {**result["fsck_warnings"], "sha256": "f" * 64}):
            with self.assertRaisesRegex(ValueError, "warning count/SHA-256"):
                payload.verified(target, {**record, "fsck_warnings": bad})

    def test_reject_unsafe_sources_destinations_and_private_scope(self):
        for url in ("http://example.com/repo", "file:///repo", "ssh://example.com/repo",
                    "https://user:password@example.com/repo", "https://127.1/repo",
                    "https://example.com/repo?token=secret", "https://example.com/repo#ref"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                payload.git_history_spec({**self.record, "repository": url})
        for destination in ("../escape", "/absolute", ".", "environment/.git"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                payload.git_history_spec({**self.record, "destination": destination})
        with self.assertRaises(ValueError):
            payload.git_history_spec({**self.record, "scope": "heldout"})
        with self.assertRaises(ValueError):
            payload.git_history_spec({**self.record, "commit": "HEAD"})
        with self.assertRaises(ValueError):
            payload.git_history_spec({**self.record, "ref_policy": "all"})
        with self.assertRaises(ValueError):
            payload.payload_target(self.root, "../outside")
        (self.root / "alias").symlink_to(self.canonical, target_is_directory=True)
        with self.assertRaises(ValueError):
            payload.payload_target(self.root, "alias/extra")

    def test_reject_outage_and_poisoned_fetch_without_publishing(self):
        target = self.root / "unpublished"
        with patch.object(payload, "_git_fetch", side_effect=ValueError("offline fixture outage")):
            with self.assertRaisesRegex(ValueError, "outage"):
                payload.acquire_git_history(self.record, target)
        self.assertFalse(target.exists())
        def shallow(gitdir, url, commit):
            self.transport(gitdir, url, commit)
            (gitdir / "shallow").write_text(self.parent + "\n")
        with patch.object(payload, "_git_fetch", side_effect=shallow):
            with self.assertRaises(ValueError):
                payload.acquire_git_history(self.record, target)
        self.assertFalse(target.exists())

    def test_hermetic_environment_discards_ambient_semantics_and_credentials(self):
        with patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": "evil", "GIT_SSH_COMMAND": "evil",
                                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/outside",
                                    "GIT_INDEX_FILE": "/outside", "HOME": "/outside"}):
            env = payload._git_env()
        for key in ("GIT_CONFIG_PARAMETERS", "GIT_SSH_COMMAND", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE"):
            self.assertNotIn(key, env)
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "https")
        self.assertEqual(env["HOME"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
