"""Fetch-only inputs are authenticated outside Git, never staged or redistributed."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import time
import shutil
import unittest
from unittest.mock import patch

from judge import heldout, image_recipe, public_input_preflight
import heldout_policy
import stage_payload


class ReferenceTests(unittest.TestCase):
    def test_qualification_cli_keeps_private_inputs_alive_only_during_run(self):
        import qualify_aa
        output = Path(self.tmp.name) / "qualification.json"
        seen = []
        def execute(task, *args):
            seen.append(task)
            self.assertEqual((task / "tests" / self.name).read_bytes(), self.body)
            return {"task": task.name, "scope": "heldout", "passed": True,
                    "error": None, "elapsed_seconds": 1}
        args = ["qualify_aa", "--task", str(self.task), "--build", "/fixture/build",
                "--image", "fixture", "--cpuset", "0-7", "--scope", "heldout",
                "--output", str(output)]
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(self.body)), \
                patch.object(qualify_aa.sys, "argv", args), \
                patch.object(qualify_aa, "run", side_effect=execute), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(qualify_aa.main(), 0)
        self.assertTrue(json.loads(output.read_text())["passed"])
        self.assertFalse(seen[0].exists())
        self.assertFalse((self.tests / self.name).exists())

    def test_acquired_view_preserves_recipe_and_public_input_identity(self):
        root = self.task.parent.parent
        environment = self.task / "environment"
        environment.mkdir()
        (environment / "Dockerfile").write_text("FROM protected-fixture\n")
        body = b"public fixture"
        (environment / "input").write_bytes(body)
        (self.task / "release.json").write_text("{}")
        payload = json.loads((self.task / "payload.json").read_text())
        payload["files"].append({
            "name": "public", "scope": "public",
            "destination": "environment/input", "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        })
        (self.task / "payload.json").write_text(json.dumps(payload))
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "tasks/task/environment"], check=True)
        before = self.inventory()
        with patch.object(image_recipe, "ROOT", root):
            expected = image_recipe.descriptor(self.task)
            with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(self.body)):
                with heldout.materialized_task(self.task) as view:
                    self.assertEqual(image_recipe.descriptor(view), expected)
                    public_input_preflight.ensure_public_inputs(view, root=root, fetch=False)
                    with heldout.materialized_task(view) as nested:
                        self.assertEqual(image_recipe.descriptor(nested), expected)
                    self.assertEqual((view / "tests" / self.name).read_bytes(), self.body)
        self.assertFalse(view.exists())
        self.assertEqual(self.inventory(), before)

    def test_unregistered_lookalike_view_cannot_access_public_checkout(self):
        fake = Path(self.tmp.name) / "heldout-acquisition-forged/task"
        fake.mkdir(parents=True)
        (fake / "payload.json").symlink_to(self.task / "payload.json")
        self.assertEqual(heldout.canonical_task(fake), fake)
        with self.assertRaisesRegex(public_input_preflight.PublicInputsError, "belong"):
            public_input_preflight.ensure_public_inputs(
                fake, root=self.task.parent.parent, fetch=False)

    def test_tampered_view_is_rejected_and_context_is_cleared(self):
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(self.body)):
            with self.assertRaisesRegex(ValueError, "protected path changed"):
                with heldout.materialized_task(self.task) as view:
                    (view / "payload.json").unlink()
                    (view / "payload.json").write_text("{}")
                    heldout.canonical_task(view)
        self.assertEqual(heldout.canonical_task(view), view)
        self.assertFalse(view.exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name) / "tasks/task"
        self.tests = self.task / "tests"
        (self.tests / "heldout").mkdir(parents=True)
        self.body = b"nonredistributable input\n"
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.name = "heldout/files/" + self.digest
        self.entry = {"scope": "heldout", "role": "input", "kind": "file",
                      "url": "https://example.org/pinned/input",
                      "bytes": len(self.body), "sha256": self.digest,
                      "license_basis": "fetch-only; no redistribution"}
        manifest = {"splits": {"private": [{"docs": [{
            "path": "case", "sha256": self.digest, "expect_sha": "a" * 64,
            "expect_len": 1}]}]}}
        path = self.tests / "heldout/manifest.json"
        path.write_text(json.dumps(manifest))
        self.lock = {"schema_version": 1, "status": "available",
                     "files": {"heldout/manifest.json": heldout.sha(path)},
                     "references": {self.name: self.entry}}
        self.save()

    def save(self):
        (self.tests / "heldout.lock.json").write_text(json.dumps(self.lock))
        (self.task / "payload.json").write_text(json.dumps({"files": [
            {**entry, "destination": "tests/" + name, "name": "fixture",
             "retention": "fetch-only"} for name, entry in self.lock["references"].items()]}))

    def inventory(self):
        return {p.relative_to(self.task).as_posix(): p.read_bytes()
                for p in self.task.rglob("*") if p.is_file()}

    def history_entry(self):
        return {"scope": "heldout", "role": "input", "kind": "git-history",
                "repository": "https://example.org/pinned/repository.git",
                "commit": "a" * 40, "ref_policy": "one-detached-commit-closure",
                "object_inventory": {"algorithm": "sha256-canonical-git-objects-v1",
                                     "object_count": 1, "bytes": 1, "sha256": "b" * 64},
                "checkout_inventory_sha256": "c" * 64,
                "license_basis": "fetch-only; no redistribution"}

    def test_histories_namespace_accepts_only_git_history_without_acquiring(self):
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            for batch in ("neovim", "mdbook", "click", "eslint"):
                name = "heldout/histories/" + batch
                self.lock["references"] = {name: self.history_entry()}
                self.assertEqual(heldout.references(self.lock), self.lock["references"])
            fetch.assert_not_called()
        for namespace in ("files", "data", "projects"):
            name = "heldout/" + namespace + "/history"
            self.lock["references"] = {name: self.history_entry()}
            self.assertEqual(heldout.references(self.lock), self.lock["references"])

    def test_histories_namespace_rejects_file_kind_including_default(self):
        for entry in (self.entry, {key: value for key, value in self.entry.items() if key != "kind"}):
            self.lock["references"] = {"heldout/histories/neovim": entry}
            with self.assertRaisesRegex(ValueError, "separate held-out input"):
                heldout.references(self.lock)

    def test_histories_namespace_rejects_path_escape_and_git_control_directories(self):
        for name in ("heldout/histories/../neovim", "heldout/histories/../../../escape",
                     "/heldout/histories/neovim", "heldout/histories//neovim",
                     "heldout/histories/.git/HEAD", "heldout/histories/.Git/HEAD"):
            with self.subTest(name=name):
                self.lock["references"] = {name: self.history_entry()}
                with self.assertRaises(ValueError):
                    heldout.references(self.lock)

    def test_acquisition_is_external_authenticated_and_cleaned_with_nested_view(self):
        before = self.inventory()
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(self.body)) as fetch:
            with heldout.materialized_task(self.task) as view:
                self.assertFalse(view.is_relative_to(self.task))
                heldout.check(view / "tests", require_available=True)
                self.assertEqual((view / "tests" / self.name).read_bytes(), self.body)
                with heldout.materialized_task(view) as nested:
                    self.assertEqual(nested, view)
                self.assertEqual(self.inventory(), before)
            self.assertFalse(view.exists())
        fetch.assert_called_once()
        self.assertEqual(self.inventory(), before)

    def test_missing_is_offline_and_catalog_style_check_does_not_fetch(self):
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            self.assertEqual(len(stage_payload.missing(self.task, "heldout")), 1)
            self.assertEqual(stage_payload.missing(self.task, "heldout", allow_fetch=True), [])
            heldout.check(self.tests, allow_missing_references=True)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                heldout.check(self.tests, require_available=True)
        fetch.assert_not_called()
        self.assertIn(self.digest, heldout_policy.private_hashes(self.task))

    def test_mismatch_and_unavailable_source_fail_without_worktree_writes(self):
        before = self.inventory()
        for data in (b"wrong", self.body + b"oversize"):
            with self.subTest(data=data), patch.object(
                    heldout.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, "size/SHA-256"):
                    with heldout.materialized_task(self.task):
                        self.fail("unauthenticated input escaped")
        with patch.object(heldout.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                with heldout.materialized_task(self.task):
                    self.fail("unavailable input escaped")
        self.assertEqual(self.inventory(), before)

    def test_concurrent_checks_report_first_failure_in_lock_order(self):
        # A later reference that fails first in wall time must not change
        # which failure is reported; every result is consumed in lock order.
        second = "heldout/files/" + "f" * 64
        self.lock["references"][second] = {**self.entry, "url": "https://example.org/pinned/second"}
        self.save()
        def fetch(url, timeout):
            if url.endswith("second"):
                raise OSError("offline")
            time.sleep(0.2)
            return io.BytesIO(b"wrong")
        with patch.object(heldout.urllib.request, "urlopen", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "size/SHA-256 mismatch: " + self.name):
                with heldout.materialized_task(self.task):
                    self.fail("unauthenticated input escaped")
        def matches(path, entry, tests=None):
            if path.name == Path(second).name:
                raise ValueError("later failure")
            time.sleep(0.2)
            return False
        with patch.object(heldout, "reference_matches", side_effect=matches):
            with self.assertRaisesRegex(ValueError, "unavailable or mismatched: " + self.name):
                heldout.check(self.tests)
        self.assertEqual([(None, 1), (None, 2), (None, 3)],
                         heldout.concurrent_outcomes(abs, [-1, 2, -3]))
        (error, _), (_, value) = heldout.concurrent_outcomes(int, ["x", "7"])
        self.assertIsInstance(error, ValueError)
        self.assertEqual(value, 7)

    def test_traversal_absolute_alias_expected_and_overlapping_paths_refused(self):
        for name in ("../escape", "/tmp/escape", "heldout/../escape",
                     "heldout/files//alias", "heldout/expected/result"):
            with self.subTest(name=name):
                self.lock["references"] = {name: self.entry}
                self.save()
                with patch.object(heldout.urllib.request, "urlopen") as fetch:
                    with self.assertRaises(ValueError):
                        with heldout.materialized_task(self.task):
                            self.fail("unsafe path escaped")
                    fetch.assert_not_called()
        self.lock["references"] = {"heldout/projects/a": self.entry,
                                   "heldout/projects/a/b": self.entry}
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            heldout.references(self.lock)

    def test_symlink_cannot_point_outside_cache(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.tests / "heldout/files").symlink_to(outside)
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            with self.assertRaises(ValueError):
                with heldout.materialized_task(self.task):
                    self.fail("symlink escaped")
            fetch.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_staging_and_retained_body_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "staging bodies"):
            stage_payload.stage(self.task, fetch=True)
        target = self.tests / self.name
        target.parent.mkdir()
        target.write_bytes(self.body)
        with self.assertRaisesRegex(ValueError, "retained in worktree"):
            stage_payload.missing(self.task, "heldout")
        with self.assertRaisesRegex(ValueError, "retained in worktree"):
            with heldout.materialized_task(self.task):
                self.fail("retained body accepted")

    def test_from_stages_retained_expected_outputs_but_never_reference_inputs(self):
        self.assert_retained_staging()

    def test_from_stages_retained_outputs_while_lock_is_still_unavailable(self):
        self.lock["status"] = "unavailable"
        self.assert_retained_staging()

    def assert_retained_staging(self):
        retained = b"independently generated expected output\n"
        name = "heldout/expected/output"
        self.lock["files"][name] = hashlib.sha256(retained).hexdigest()
        self.save()
        payload_path = self.task / "payload.json"
        payload = json.loads(payload_path.read_text())
        payload["files"].append({"name": "output", "destination": "tests/" + name,
                                 "scope": "heldout", "bytes": len(retained),
                                 "sha256": self.lock["files"][name]})
        payload_path.write_text(json.dumps(payload))
        source = Path(self.tmp.name) / "source"
        (source / "tests/heldout/expected").mkdir(parents=True)
        (source / "tests" / name).write_bytes(retained)
        # A complete source directory may contain reference bytes too. They
        # must be ignored, not copied alongside retained expected outputs.
        reference = source / "tests" / self.name
        reference.parent.mkdir()
        reference.write_bytes(self.body)
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            self.assertEqual(stage_payload.stage(self.task, source=source, scope="heldout"), 1)
            self.assertEqual((self.tests / name).read_bytes(), retained)
            self.assertFalse((self.tests / self.name).exists())
            self.assertEqual(stage_payload.stage(self.task, source=source, scope="heldout"), 0)
            self.assertEqual(len(stage_payload.missing(self.task, "heldout")), 1)
            heldout.check(self.tests, allow_missing_references=True)
            for supplied_source in (None, source):
                with self.assertRaisesRegex(ValueError, "staging bodies"):
                    stage_payload.stage(self.task, source=supplied_source, fetch=True, scope="heldout")
            fetch.assert_not_called()
        # A post-staging check still refuses to call this a complete payload.
        with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
            stage_payload.check(self.task, "heldout")
        self.assertFalse((self.tests / self.name).exists())
        # Supplying a real reference body in the worktree fails preflight even
        # on the permitted --from path; no reference authentication is waived.
        destination = self.tests / self.name
        destination.parent.mkdir()
        destination.write_bytes(self.body)
        with self.assertRaisesRegex(ValueError, "retained in worktree"):
            stage_payload.stage(self.task, source=source, scope="heldout")

    def test_pointer_only_retained_output_fails_before_network(self):
        name = "heldout/expected/output"
        target = self.tests / name
        target.parent.mkdir()
        target.write_bytes(b"version https://git-lfs.github.com/spec/v1\n"
                           b"oid sha256:" + b"a" * 64 + b"\nsize 1\n")
        self.lock["files"][name] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.save()
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            with self.assertRaisesRegex(ValueError, "git lfs pull"):
                with heldout.materialized_task(self.task):
                    self.fail("pointer accepted")
            fetch.assert_not_called()

    def test_payload_lock_pin_drift_and_missing_license_are_rejected(self):
        payload = json.loads((self.task / "payload.json").read_text())
        payload["files"][0]["bytes"] += 1
        (self.task / "payload.json").write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "pins differ"):
            stage_payload.missing(self.task)
        self.entry.pop("license_basis")
        with self.assertRaisesRegex(ValueError, "license basis"):
            heldout.references(self.lock)

    def test_verifier_wrapper_acquires_before_body_and_public_plumbing_is_offline(self):
        @heldout.acquired
        def execute(task, plumbing=False):
            if plumbing:
                return Path(task)
            self.assertEqual((Path(task) / "tests" / self.name).read_bytes(), self.body)
            return Path(task)
        with patch.object(heldout.urllib.request, "urlopen", return_value=io.BytesIO(self.body)) as fetch:
            result = execute(self.task)
            self.assertFalse(result.exists())
            self.assertEqual(execute(self.task, plumbing=True), self.task)
            fetch.assert_called_once()

    @unittest.skipUnless(shutil.which("git"), "Git executable required")
    def test_reference_hashes_reject_public_byte_collisions(self):
        root = self.task.parent.parent
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        environment = self.task / "environment"
        environment.mkdir()
        (environment / "input").write_bytes(self.body)
        with self.assertRaisesRegex(ValueError, "Held-out bytes in public"):
            heldout_policy.check_repository(root, [self.task])

    def test_build_time_archive_step_validates_only_metadata_and_does_not_acquire_references(self):
        self.lock["archives"] = []
        self.save()
        with patch.object(heldout.urllib.request, "urlopen") as fetch:
            heldout.fetch_archives(self.tests, self.task.parent / "projects")
        fetch.assert_not_called()
        self.assertFalse((self.tests / self.name).exists())
