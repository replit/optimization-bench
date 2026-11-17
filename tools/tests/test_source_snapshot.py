import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import export_source_snapshot

IDENTITY = {"GIT_AUTHOR_NAME": "Snapshot Test Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Snapshot Test Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}


def run(root, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()


class SourceSnapshotTests(unittest.TestCase):
    def fixture(self, root):
        donor = root / "donor"
        task = donor / "tasks/example"
        public = task / "environment/workload/inputs/files/original"
        heldout = task / "tests/heldout/files/original"
        for path, body in ((public, b"public original"), (heldout, b"never-copy-heldout")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        (donor / "runner.py").write_text("print('source code')\n")
        (donor / "datasets").mkdir()
        (donor / "datasets/protected-ledger.json").write_text('{"review":"private-provenance-not-cleared"}')
        operator = donor / "judge/hosts/registry.json"
        operator.parent.mkdir(parents=True)
        operator.write_text('{"hosts":[{"host_id":"private-fixture"}]}')
        (operator.parent / "registry.schema.json").write_text("{}")
        entries = []
        for path, scope in ((public, "public"), (heldout, "heldout")):
            body = path.read_bytes()
            entries.append({"scope": scope, "kind": "file", "destination": path.relative_to(task).as_posix(),
                            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
        (task / "payload.json").write_text(json.dumps({"files": entries}))
        run(donor, "init", "--quiet")
        run(donor, "add", "--all")
        run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "private donor fixture")
        run(donor, "remote", "add", "origin", "https://example.invalid/private/source.git")
        return donor, public.relative_to(donor), heldout.relative_to(donor)

    def test_fresh_git_has_no_donor_objects_origin_operator_registry_or_input_bodies(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, public, heldout = self.fixture(root)
            old = run(donor, "rev-parse", "HEAD")
            receipt = export_source_snapshot.export(donor, root / "export")
            output = root / "export"
            self.assertEqual(run(output, "rev-list", "--all", "--count"), "1")
            self.assertEqual(run(output, "remote", "-v"), "")
            absent = subprocess.run(["git", "cat-file", "-e", old + "^{commit}"],
                                    cwd=output, capture_output=True)
            self.assertNotEqual(absent.returncode, 0)
            self.assertFalse((output / "judge/hosts/registry.json").exists())
            self.assertTrue((output / "judge/hosts/registry.schema.json").is_file())
            self.assertFalse((output / public).exists())
            self.assertFalse((output / heldout).exists())
            self.assertEqual((output / "runner.py").read_bytes(), (donor / "runner.py").read_bytes())
            self.assertEqual((output / "datasets/protected-ledger.json").read_bytes(),
                             (donor / "datasets/protected-ledger.json").read_bytes())
            self.assertFalse(receipt["donor_git_history_copied"])
            self.assertEqual(receipt["publication_clearance"], "not-evaluated")

    def test_selected_authenticated_public_data_is_local_ignored_and_never_includes_heldout(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, public, heldout = self.fixture(root)
            receipt = export_source_snapshot.export(donor, root / "export", ["example"])
            output = root / "export"
            self.assertEqual((output / public).read_bytes(), (donor / public).read_bytes())
            self.assertFalse((output / heldout).exists())
            self.assertEqual(run(output, "ls-files", "--", str(public)), "")
            self.assertEqual(receipt["included_public_inputs"], 1)

    def test_export_refuses_uncommitted_code_and_does_not_make_a_partial_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, _, _ = self.fixture(root)
            (donor / "runner.py").write_text("uncommitted mutation\n")
            with self.assertRaises(subprocess.CalledProcessError):
                export_source_snapshot.export(donor, root / "export")
            self.assertFalse((root / "export").exists())

    def retained_fixture(self, root):
        donor, public, heldout = self.fixture(root)
        task = donor / "tasks/example"
        recovery = b"synthetic authenticated recovery body\n"
        digest = hashlib.sha256(recovery).hexdigest()
        source = task / "public/files" / digest
        source.parent.mkdir(parents=True)
        source.write_bytes(recovery)
        payload = json.loads((task / "payload.json").read_text())
        destination = "environment/workload/inputs/files/recovery"
        payload["files"].append({"scope": "public", "kind": "repo-lfs", "source": source.relative_to(task).as_posix(),
                                 "destination": destination, "bytes": len(recovery), "sha256": digest,
                                 "reason": "synthetic public source-export recovery fixture"})
        unlisted = task / "environment/workload/inputs/files/unlisted"
        unlisted.write_bytes(b"unlisted even though distribution is git")
        payload["files"].append({"scope": "public", "kind": "file", "distribution": "git",
            "destination": unlisted.relative_to(task).as_posix(), "bytes": unlisted.stat().st_size,
            "sha256": hashlib.sha256(unlisted.read_bytes()).hexdigest()})
        (task / "payload.json").write_text(json.dumps(payload))
        notice = task / "notices/NOTICE.txt"
        notice.parent.mkdir()
        notice.write_text("Synthetic test-fixture attribution; not publication approval.\n")
        original = payload["files"][0]
        rows = [{"source_path": public.relative_to("tasks/example").as_posix(),
                 "bytes": original["bytes"], "sha256": original["sha256"]},
                {"source_path": source.relative_to(task).as_posix(), "staged_destination": destination,
                 "bytes": len(recovery), "sha256": digest}]
        for row in rows:
            row.update({"source_license": "CC0-1.0", "source_license_url": "https://creativecommons.org/publicdomain/zero/1.0/"})
        inventory = donor / "reviewed-public-fixtures.json"
        inventory.write_text(json.dumps({"task": "example", "task_root": "tasks/example", "path_base": "task_root",
            "count": 2, "bytes": sum(row["bytes"] for row in rows), "inputs": rows, "publication_authorized": False,
            "rights_basis": "Synthetic fixture metadata, not real redistribution clearance.",
            "notice_conditions": ["Retain the synthetic notice."],
            "required_notices": [{"path": notice.relative_to(task).as_posix(),
                                  "sha256": hashlib.sha256(notice.read_bytes()).hexdigest()}]}))
        (donor / ".gitattributes").write_text("tasks/example/public/files/* filter=lfs diff=lfs merge=lfs -text\n")
        run(donor, "config", "filter.lfs.process", "")
        run(donor, "config", "filter.lfs.clean", "cat")
        run(donor, "config", "filter.lfs.required", "false")
        run(donor, "add", "--all")
        run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "explicit synthetic fixture inventory")
        return donor, inventory, public, heldout, source.relative_to(donor), unlisted.relative_to(donor), notice.relative_to(donor)

    def test_reviewed_public_set_is_exact_hydrated_git_content_and_survives_fresh_clone(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, inventory, public, heldout, recovery, unlisted, notice = self.retained_fixture(root)
            old = run(donor, "rev-parse", "HEAD")
            output = root / "export"
            receipt = export_source_snapshot.export(donor, output, retention_inventories=[inventory])
            clone = root / "clone"
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.process=",
                            "-c", "filter.lfs.smudge=cat", "-c", "filter.lfs.required=false",
                            "clone", "--quiet", "--no-local", str(output), str(clone)], check=True)
            for name in (public, recovery, notice):
                self.assertEqual((clone / name).read_bytes(), (donor / name).read_bytes())
                self.assertEqual(subprocess.check_output(["git", "show", "HEAD:" + name.as_posix()], cwd=clone),
                                 (donor / name).read_bytes())
                self.assertTrue(run(clone, "check-attr", "filter", "--", name.as_posix()).endswith(": unset"))
                subprocess.run(["git", "-c", "filter.lfs.process=", "-c", "filter.lfs.clean=false",
                                "-c", "filter.lfs.required=true", "diff", "--exit-code", "--", name.as_posix()],
                               cwd=clone, check=True, capture_output=True)
            self.assertFalse((clone / heldout).exists())
            self.assertFalse((clone / unlisted).exists())
            self.assertFalse((clone / ".git/lfs/objects").exists())
            self.assertEqual(run(clone, "rev-list", "--all", "--count"), "1")
            self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", old], cwd=clone, capture_output=True).returncode, 0)
            self.assertEqual(receipt["retained_public_fixture_inventories"][0]["fixture_count"], 2)
            self.assertEqual(receipt["publication_clearance"], "not-evaluated")

    def test_reviewed_inventory_cannot_retain_heldout_or_pointer_instead_of_body(self):
        for failure in ("heldout", "pointer", "notice"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
                root = Path(temporary)
                donor, inventory, public, heldout, recovery, unlisted, notice = self.retained_fixture(root)
                if failure == "heldout":
                    value = json.loads(inventory.read_text())
                    row = value["inputs"][0]
                    row.update({"source_path": heldout.relative_to("tasks/example").as_posix(),
                                "bytes": (donor / heldout).stat().st_size,
                                "sha256": hashlib.sha256((donor / heldout).read_bytes()).hexdigest()})
                    value["bytes"] = sum(entry["bytes"] for entry in value["inputs"])
                    inventory.write_text(json.dumps(value))
                elif failure == "pointer":
                    (donor / recovery).write_bytes(export_source_snapshot.POINTER + b"oid sha256:" + b"a" * 64 + b"\nsize 37\n")
                else:
                    (donor / notice).write_text("corrupted fixture notice\n")
                run(donor, "add", "--all")
                run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "invalid synthetic fixture selection")
                with self.assertRaises(ValueError):
                    export_source_snapshot.export(donor, root / "export", retention_inventories=[inventory])
                self.assertFalse((root / "export").exists())

    def scorer_fixture(self, root):
        donor, public, heldout = self.fixture(root)
        task = donor / "tasks/example"
        rows = json.loads((task / "payload.json").read_text())
        policy, locked = {}, {}
        for name, body in (("tests/heldout/manifest.json", b'{"fixture":"original case manifest"}\n'),
                           ("tests/heldout/references/sources.json", b'{"fixture":"authored source pins"}\n'),
                           ("tests/heldout/references/selection-freeze.json", b'{"fixture":"authored query receipt"}\n'),
                           ("tests/heldout/expected/original", b"never export this golden body")):
            path = task / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            digest = hashlib.sha256(body).hexdigest()
            rows["files"].append({"destination": name, "name": name, "scope": "heldout",
                                  "bytes": len(body), "sha256": digest})
            locked[name.removeprefix("tests/")] = digest
            if "/expected/" not in name:
                policy[name] = (len(body), digest)
        (task / "payload.json").write_text(json.dumps(rows))
        (task / "tests/heldout.lock.json").write_text(json.dumps(
            {"schema_version": 1, "status": "available", "files": locked, "references": {}, "archives": []}))
        (task / ".gitattributes").write_text(
            "tests/heldout/manifest.json filter=lfs diff=lfs merge=lfs -text\n"
            "tests/heldout/references/** filter=lfs diff=lfs merge=lfs -text\n")
        (donor / ".gitattributes").write_text("* text=auto eol=lf\n")
        run(donor, "config", "filter.lfs.process", "")
        run(donor, "config", "filter.lfs.clean", "cat")
        run(donor, "config", "filter.lfs.required", "false")
        run(donor, "add", "--all")
        run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "synthetic authored metadata")
        return donor, public, heldout, policy

    def test_scorer_bundle_preserves_nested_task_attributes_and_exact_original_git_bytes(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, public, heldout, policy = self.scorer_fixture(root)
            attributes = (donor / "tasks/example/.gitattributes").read_bytes()
            with patch.dict(export_source_snapshot.SCORER_METADATA, {"example": policy}):
                receipt = export_source_snapshot.export(donor, root / "export", scorer_tasks=["example"])
            clone = root / "clone"
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "--quiet", "--no-local",
                            str(root / "export"), str(clone)], check=True)
            for name, (size, digest) in policy.items():
                relative = "scorer-metadata/example/" + name
                body = (clone / relative).read_bytes()
                self.assertEqual((len(body), hashlib.sha256(body).hexdigest()), (size, digest))
                self.assertEqual(subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=clone), body)
                self.assertTrue(run(clone, "check-attr", "filter", "--", relative).endswith(": unset"))
                self.assertTrue(run(clone, "check-attr", "text", "--", relative).endswith(": unset"))
                subprocess.run(["git", "-c", "filter.lfs.process=", "-c", "filter.lfs.clean=false",
                                "-c", "filter.lfs.required=true", "diff", "--exit-code", "--", relative],
                               cwd=clone, check=True, capture_output=True)
                self.assertFalse((clone / "tasks/example" / name).exists())
            self.assertEqual((clone / "tasks/example/.gitattributes").read_bytes(), attributes)
            self.assertTrue(run(clone, "check-attr", "filter", "--",
                                "tasks/example/tests/heldout/manifest.json").endswith(": lfs"))
            self.assertFalse((clone / "tasks/example/tests/heldout/expected/original").exists())
            self.assertFalse((clone / public).exists())
            self.assertFalse((clone / heldout).exists())
            self.assertFalse((clone / ".git/lfs/objects").exists())
            self.assertEqual(run(clone, "rev-list", "--all", "--count"), "1")
            self.assertEqual(receipt["scorer_metadata_bundles"][0]["files"], 3)
            self.assertFalse(receipt["scorer_metadata_bundles"][0]["data_golden_or_input_bodies_included"])

    def test_scorer_metadata_rejects_corrupt_original_pins_inputs_and_golden_paths_without_partial_export(self):
        for failure in ("corrupt", "lock", "input-role", "golden"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
                root = Path(temporary)
                donor, _, _, policy = self.scorer_fixture(root)
                task = donor / "tasks/example"
                if failure == "corrupt":
                    path = task / "tests/heldout/references/sources.json"
                    body = path.read_bytes()
                    path.write_bytes(bytes([body[0] ^ 1]) + body[1:])
                elif failure == "lock":
                    lock = json.loads((task / "tests/heldout.lock.json").read_text())
                    lock["files"]["heldout/references/sources.json"] = "0" * 64
                    (task / "tests/heldout.lock.json").write_text(json.dumps(lock))
                elif failure == "input-role":
                    payload = json.loads((task / "payload.json").read_text())
                    next(row for row in payload["files"] if row["destination"] ==
                         "tests/heldout/references/sources.json")["role"] = "input"
                    (task / "payload.json").write_text(json.dumps(payload))
                else:
                    path = task / "tests/heldout/expected/original"
                    policy[path.relative_to(task).as_posix()] = (
                        path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
                run(donor, "add", "--all")
                run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty",
                    "-m", "invalid synthetic metadata")
                with patch.dict(export_source_snapshot.SCORER_METADATA, {"example": policy}), self.assertRaises(ValueError):
                    export_source_snapshot.export(donor, root / "export", scorer_tasks=["example"])
                self.assertFalse((root / "export").exists())

    def test_external_scorer_originals_use_existing_from_layout_without_staging_task_or_other_assets(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, _, _, policy = self.scorer_fixture(root)
            originals = root / "verified-originals"
            for name in policy:
                source = donor / "tasks/example" / name
                destination = originals / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
                size, digest = policy[name]
                source.write_bytes(export_source_snapshot.POINTER +
                                   f"oid sha256:{digest}\nsize {size}\n".encode())
            run(donor, "add", "--all")
            run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "unhydrated synthetic metadata")
            with patch.dict(export_source_snapshot.SCORER_METADATA, {"example": policy}):
                with self.assertRaisesRegex(ValueError, "Missing/corrupt original authored"):
                    export_source_snapshot.export(donor, root / "missing", scorer_tasks=["example"])
                export_source_snapshot.export(donor, root / "export", scorer_tasks=["example"],
                                              scorer_originals=originals)
            self.assertFalse((root / "missing").exists())
            for name in policy:
                self.assertTrue((donor / "tasks/example" / name).read_bytes().startswith(export_source_snapshot.POINTER))
                self.assertEqual((root / "export/scorer-metadata/example" / name).read_bytes(),
                                 (originals / name).read_bytes())
            export_source_snapshot.export(root / "export", root / "without-selected-metadata")
            self.assertFalse((root / "without-selected-metadata/scorer-metadata").exists())

    def test_reuses_donor_local_identity_without_copying_git_configuration(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, _, _ = self.fixture(root)
            run(donor, "config", "user.name", "Local Snapshot Fixture")
            run(donor, "config", "user.email", "local-fixture@example.invalid")
            run(donor, "config", "credential.helper", "unused-fixture")
            clean = {key: value for key, value in os.environ.items()
                     if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
            with patch.dict(os.environ, clean, clear=True):
                export_source_snapshot.export(donor, root / "export")
            self.assertEqual(run(root / "export", "show", "-s", "--format=%an"), "Local Snapshot Fixture")
            config = (root / "export/.git/config").read_text()
            self.assertNotIn("credential", config)
            self.assertNotIn("origin", config)

    def test_escaping_source_symlink_is_rejected_without_copying_external_data(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, IDENTITY):
            root = Path(temporary)
            donor, _, _ = self.fixture(root)
            (donor / "bad-link").symlink_to(root / "not-source")
            run(donor, "add", "bad-link")
            run(donor, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "unsafe link fixture")
            with self.assertRaisesRegex(ValueError, "symlink escapes"):
                export_source_snapshot.export(donor, root / "export")
            self.assertFalse((root / "export").exists())


if __name__ == "__main__":
    unittest.main()
