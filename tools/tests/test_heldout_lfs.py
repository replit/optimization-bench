import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from judge import heldout, score, image_recipe
from judge.task_catalog import task_paths
from heldout_policy import check_repository
from stage_payload import authentic
import update_catalog

POINTER = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 2000000\n"
RULE = "tasks/*/tests/heldout/files/** filter=lfs diff=lfs merge=lfs -text\n"


class HeldoutLfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.task = self.root / "tasks/test"
        self.tests = self.task / "tests"
        (self.tests / "heldout/files").mkdir(parents=True)
        (self.task / "environment").mkdir()
        (self.root / ".gitattributes").write_text(RULE)
        self.payload = {"files": []}
        self.lock = {"schema_version": 1, "status": "unavailable", "files": {}}
        self.manifest = {"splits": {}}
        self.write()

    def write(self):
        manifest = self.tests / "heldout/manifest.json"
        manifest.write_text(json.dumps(self.manifest))
        self.lock["files"]["heldout/manifest.json"] = heldout.sha(manifest)
        (self.tests / "heldout.lock.json").write_text(json.dumps(self.lock))
        (self.task / "payload.json").write_text(json.dumps(self.payload))

    def policy(self):
        return check_repository(self.root, [self.task])

    def test_pointer_rejected_even_when_its_own_hash_is_locked(self):
        path = self.tests / "heldout/files/data"
        path.write_bytes(POINTER)
        self.lock["files"]["heldout/files/data"] = hashlib.sha256(POINTER).hexdigest()
        self.write()
        for operation in (
            lambda: heldout.check(self.tests),
            lambda: authentic(path, {"scope": "heldout", "sha256": hashlib.sha256(POINTER).hexdigest()}),
        ):
            with self.assertRaisesRegex(ValueError, r"held-out LFS object not fetched \(git lfs pull\)"):
                operation()

    def test_available_real_content_and_expected_commitment(self):
        data = b"original heldout"
        sha = hashlib.sha256(data).hexdigest()
        (self.tests / "heldout/files" / sha).write_bytes(data)
        self.lock.update(status="available", files={"heldout/files/" + sha: sha})
        self.manifest = {"splits": {"private": [{"docs": [
            {"sha256": sha, "expect_sha": hashlib.sha256(b"output").hexdigest(), "expect_len": 6}
        ]}]}}
        self.write()
        self.assertEqual(heldout.check(self.tests, require_available=True)["status"], "available")
        self.policy()

    def test_real_lfs_clean_smudge_roundtrip_and_unfetched_checkout(self):
        subprocess.run(["git", "-C", str(self.root), "lfs", "install", "--local"],
                       check=True, stdout=subprocess.DEVNULL)
        data = b"verified original bytes\n" * 100
        name = "tasks/test/tests/heldout/files/" + hashlib.sha256(data).hexdigest()
        path = self.root / name
        path.write_bytes(data)
        subprocess.run(["git", "-C", str(self.root), "add", ".gitattributes", name], check=True)
        pointer = subprocess.check_output(["git", "-C", str(self.root), "show", ":" + name])
        self.assertTrue(pointer.startswith(b"version https://git-lfs.github.com/spec/v1\n"))
        self.assertIn(b"oid sha256:" + hashlib.sha256(data).hexdigest().encode(), pointer)
        restored = subprocess.check_output(
            ["git", "-C", str(self.root), "lfs", "smudge"], input=pointer,
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "0"})
        self.assertEqual(restored, data)
        path.write_bytes(pointer)
        self.lock["files"]["heldout/files/" + path.name] = hashlib.sha256(data).hexdigest()
        self.write()
        with self.assertRaisesRegex(ValueError, r"held-out LFS object not fetched \(git lfs pull\)"):
            heldout.check(self.tests)
        path.write_bytes(restored)
        heldout.check(self.tests)

    def test_catalog_counts_unfetched_private_pointer_without_authenticating_it(self):
        from stage_payload import missing
        path = self.tests / "heldout/files/private"
        path.write_bytes(POINTER)
        entry = {"scope": "heldout", "name": "private",
                 "destination": "tests/heldout/files/private", "bytes": len(POINTER),
                 "sha256": hashlib.sha256(POINTER).hexdigest()}
        self.payload["files"] = [entry]
        self.write()
        self.assertEqual(missing(self.task, "heldout", allow_unfetched_private=True), [entry])
        with self.assertRaisesRegex(ValueError, "git lfs pull"):
            missing(self.task, "heldout")
        with self.assertRaisesRegex(ValueError, "git lfs pull"):
            authentic(path, entry)

    def test_private_project_root_requires_complete_available_lock(self):
        self.assertEqual(heldout.project_root(self.tests, self.root), self.root)
        project = self.tests / "heldout/projects/recharts"
        project.mkdir(parents=True)
        (project / "input.js").write_bytes(b"export const x = 1;")
        with self.assertRaisesRegex(ValueError, "Uncommitted"):
            heldout.project_root(self.tests, self.root)
        self.lock["files"]["heldout/projects/recharts/input.js"] = heldout.sha(project / "input.js")
        self.write()
        with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
            heldout.project_root(self.tests, self.root)
        data = b"private case"
        sha = hashlib.sha256(data).hexdigest()
        (self.tests / "heldout/files" / sha).write_bytes(data)
        self.lock["files"]["heldout/files/" + sha] = sha
        self.lock["status"] = "available"
        self.manifest = {"splits": {"heldout": [{"docs": [
            {"path": "case", "sha256": sha, "expect_sha": "a" * 64, "expect_len": 1}
        ]}]}}
        self.write()
        self.assertEqual(heldout.project_root(self.tests, self.root), self.tests / "heldout/projects")

    def test_large_private_requires_lfs_and_no_environment_lfs(self):
        path = self.tests / "heldout/files/data"
        path.write_bytes(b"x" * 1_000_001)
        self.policy()
        (self.root / ".gitattributes").write_text("")
        with self.assertRaisesRegex(ValueError, "must be LFS-tracked"):
            self.policy()
        path.unlink()
        (self.root / ".gitattributes").write_text("tasks/*/environment/** filter=lfs\n")
        (self.task / "environment/file").write_text("public")
        with self.assertRaisesRegex(ValueError, "LFS forbidden"):
            self.policy()

    def test_private_metadata_overlay_never_replaces_executable_code(self):
        fallback = self.task / "environment"
        (fallback / "task.py").write_text("original worker")
        (fallback / "corpus.json").write_text('{"parents": "public"}')
        overlay = self.tests / "heldout/workload"
        overlay.mkdir()
        (overlay / "corpus.json").write_text('{"parents": "private"}')
        self.lock["status"] = "available"
        self.lock["files"]["heldout/workload/corpus.json"] = heldout.sha(overlay / "corpus.json")
        self.write()
        with heldout.workload_view(self.tests, fallback) as view:
            self.assertEqual((view / "task.py").read_text(), "original worker")
            self.assertEqual(json.loads((view / "corpus.json").read_text())["parents"], "private")
        self.assertFalse(view.exists())
        (overlay / "task.py").write_text("replacement worker")
        with self.assertRaisesRegex(ValueError, "only JSON/text"):
            with heldout.workload_view(self.tests, fallback):
                pass
        (overlay / "task.py").unlink()
        (overlay / "corpus.json").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Uncommitted private"):
            with heldout.workload_view(self.tests, fallback):
                pass
        with heldout.workload_view(self.tests, fallback, enabled=False) as view:
            self.assertEqual(view, fallback)
        (overlay / "corpus.json").unlink()
        (overlay / "corpus.json").symlink_to(fallback / "corpus.json")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            with heldout.workload_view(self.tests, fallback):
                pass

    def test_public_body_size_limit_not_metadata_size(self):
        path = self.task / "environment/body"
        path.write_bytes(b"x" * 1_000_001)
        self.payload["files"] = [{"scope": "public", "destination": "environment/body"}]
        self.write()
        subprocess.run(["git", "-C", str(self.root), "add", "tasks/test/environment/body"], check=True)
        with self.assertRaisesRegex(ValueError, "exceeds 1 MB"):
            self.policy()
        self.payload["files"] = []
        self.write()
        self.policy()

    def test_disjointness_bytes_digest_and_cross_task(self):
        private = b"private input"
        sha = hashlib.sha256(private).hexdigest()
        self.lock["files"]["heldout/files/" + sha] = sha
        self.write()
        environment = self.task / "environment/leak"
        for value, error in ((private, "Held-out bytes"), (sha.encode(), "Held-out sha256")):
            environment.write_bytes(value)
            with self.assertRaisesRegex(ValueError, error):
                self.policy()
        environment.unlink()
        other = self.root / "tasks/other"
        (other / "environment").mkdir(parents=True)
        (other / "environment/payload-public.json").write_text(json.dumps({"sha256": sha}))
        (other / "tests").mkdir()
        (other / "tests/heldout.lock.json").write_text(json.dumps({"files": {}}))
        (other / "payload.json").write_text('{"files":[]}')
        with self.assertRaisesRegex(ValueError, "Held-out sha256"):
            check_repository(self.root, [self.task, other])

    def test_empty_input_collision_is_exempt_and_paths_are_reported(self):
        empty_sha = hashlib.sha256(b"").hexdigest()
        private = self.tests / "heldout/files" / empty_sha
        private.write_bytes(b"")
        self.lock["files"]["heldout/files/" + empty_sha] = empty_sha
        self.write()
        public = self.task / "environment/empty.py"
        public.write_bytes(b"")
        (self.task / "environment/manifest.json").write_text(json.dumps({"sha256": empty_sha}))
        report = {}
        self.assertEqual(check_repository(self.root, [self.task], report=report), [])
        self.assertEqual(report["exempt_empty_files"], sorted([
            private.relative_to(self.root).as_posix(), public.relative_to(self.root).as_posix()]))

    def test_one_byte_input_collision_is_not_exempt(self):
        raw = b"x"
        sha = hashlib.sha256(raw).hexdigest()
        (self.tests / "heldout/files" / sha).write_bytes(raw)
        self.lock["files"]["heldout/files/" + sha] = sha
        self.write()
        (self.task / "environment/collision").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "Held-out bytes"):
            self.policy()

    def test_private_record_cannot_target_environment(self):
        self.payload["files"] = [{"scope": "heldout", "destination": "environment/private"}]
        self.write()
        with self.assertRaisesRegex(ValueError, "scope/destination"):
            self.policy()

    def test_expected_output_coincidence_is_reported_but_inputs_stay_disjoint(self):
        raw, expected = b"private independent input", b"coinciding result"
        sha, output_sha = hashlib.sha256(raw).hexdigest(), hashlib.sha256(expected).hexdigest()
        (self.tests / "heldout/files" / sha).write_bytes(raw)
        (self.tests / "heldout/expected").mkdir()
        (self.tests / "heldout/expected/hidden-case").write_bytes(expected)
        self.lock["files"].update({
            "heldout/files/" + sha: sha, "heldout/expected/hidden-case": output_sha})
        self.payload["files"] = [{"scope": "heldout", "sha256": output_sha,
                                 "destination": "tests/heldout/expected/hidden-case"}]
        self.manifest = {"splits": {"heldout": [{"docs": [{
            "path": "hidden-case", "sha256": sha, "expect_sha": output_sha, "expect_len": len(expected)
        }]}]}}
        self.write()
        workload = self.task / "environment/workload"
        workload.mkdir()
        (workload / "baseline-outputs.json").write_text(json.dumps([
            {"id": "public-case", "sha256": output_sha, "bytes": len(expected)}]))
        rows = self.policy()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"],
                         "expected-output coincides with public output: test/hidden-case == test/public-case")
        (workload / "accidental-input-leak").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "Held-out bytes"):
            self.policy()

    def test_catalog_never_refreshes_private_inputs_or_exports_their_hashes(self):
        (self.task / "task.json").write_text("{}")
        artifact = self.task / "environment/public-artifacts.json"
        artifact.write_text(json.dumps({"files": {
            "tests/heldout.lock.json": "a" * 64,
            "tests/heldout/manifest.json": "b" * 64,
            "upstream-owned.c": "c" * 64
        }}))
        private = self.tests / "heldout/source.lock.json"
        original = json.dumps({"files": {"tests/heldout/manifest.json": "d" * 64}})
        private.write_text(original)
        with mock.patch.object(update_catalog, "ROOT", self.root):
            update_catalog.refresh_bindings()
        self.assertEqual(json.loads(artifact.read_text()), {"files": {"upstream-owned.c": "c" * 64}})
        self.assertEqual(private.read_text(), original)

    def test_public_input_preflight_failure_prevents_image_build(self):
        (self.task / "environment/public-input-packet.json").write_text(json.dumps({
            "schema": "optimbench-public-input-packet-v1", "mounts": ["inputs/files"]}))
        error = score.public_input_preflight.PublicInputsError("Public packet rejected")
        with mock.patch.object(image_recipe, "descriptor", return_value={"recipe_sha256": "a" * 64}), \
             mock.patch.object(score, "image_id", side_effect=subprocess.CalledProcessError(1, "docker")), \
             mock.patch.object(score.subprocess, "run") as run, \
             mock.patch.object(score.public_input_preflight, "ensure_public_inputs", side_effect=error) as preflight:
            with self.assertRaises(score.public_input_preflight.PublicInputsError) as raised:
                score.ensure_image(self.task, None, self.root)
            self.assertIs(raised.exception, error)
            preflight.assert_called_once_with(self.task)
            run.assert_not_called()

    def test_every_real_agent_docker_context_excludes_tests(self):
        tasks = task_paths(ROOT)
        for task in tasks:
            recipe = image_recipe.descriptor(task)
            with self.subTest(task=task.name), \
                 mock.patch.object(score, "image_id", side_effect=[
                     subprocess.CalledProcessError(1, "docker"), "image-id"
                  ]), mock.patch.object(score.subprocess, "run") as run, \
                 mock.patch.object(image_recipe, "descriptor", return_value=recipe), \
                 mock.patch.object(score.public_input_preflight, "ensure_public_inputs") as preflight:
                self.assertEqual(score.ensure_image(task, None, self.root), "image-id")
                if (task / "environment/public-input-packet.json").is_file():
                    preflight.assert_called_once_with(task)
                else:
                    preflight.assert_not_called()
                command = run.call_args.args[0]
                self.assertEqual(command[-1], str(task / "environment"))
                context = Path(command[-1]).resolve()
                self.assertFalse((task / "tests").resolve().is_relative_to(context))
                for path in context.rglob("*"):
                    self.assertTrue(path.resolve().is_relative_to(context))


if __name__ == "__main__":
    unittest.main()
