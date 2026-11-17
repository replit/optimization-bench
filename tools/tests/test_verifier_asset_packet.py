import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from judge import heldout
from tools import prepare_verifier_assets as packets


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class VerifierAssetPacketTests(unittest.TestCase):
    def fixture(self, name="example", pointer=False, references=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source"
        task = source / "tasks" / name
        tests = task / "tests"
        tests.mkdir(parents=True)
        (tests / "task.py").write_text("ORIGINAL_VERIFIER = True\n")
        (tests / "guard.json").write_text('{"original_guard":true}\n')
        original = b"synthetic original input\n"
        output = b"synthetic original output\n"
        if pointer:
            output = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 99\n"
        data = {"tests/heldout/files/" + sha(original): original,
                "tests/heldout/expected/answer.bin": output,
                "tests/heldout/references/lock-only.json": b'{"original_locked_metadata":true}\n'}
        manifest = {"splits": {"heldout": [{"docs": [
            {"path": "case", "sha256": sha(original), "expect_sha": sha(output), "expect_len": len(output)}
        ]}]}}
        data["tests/heldout/manifest.json"] = (json.dumps(manifest) + "\n").encode()
        lock = {"schema_version": 1, "status": "available", "references": {}, "archives": [],
                "files": {name.removeprefix("tests/"): sha(raw) for name, raw in data.items()}}
        for name in ("task.py", "guard.json"):
            lock["files"][name] = sha((tests / name).read_bytes())
        (tests / "heldout.lock.json").write_text(json.dumps(lock) + "\n")
        payload = {"files": [
            {"name": name, "destination": name, "scope": "heldout", "bytes": len(raw), "sha256": sha(raw)}
            for name, raw in data.items() if "lock-only" not in name]}
        if references:
            name = "heldout/files/" + sha(original)
            reference = {"scope": "heldout", "role": "input", "license_basis": "fetch-only; no redistribution",
                         "url": "https://fixture.invalid/pinned-input", "bytes": len(original), "sha256": sha(original)}
            lock["files"].pop(name)
            lock["references"][name] = reference
            record = next(row for row in payload["files"] if row["destination"] == "tests/" + name)
            record.update(reference, retention="fetch-only")
            data.pop("tests/" + name)
            (tests / "heldout.lock.json").write_text(json.dumps(lock) + "\n")
        (task / "payload.json").write_text(json.dumps(payload) + "\n")
        (source / ".gitignore").write_text("__pycache__/\n")
        (source / "judge").mkdir()
        shutil.copyfile(heldout.__file__, source / "judge/heldout.py")
        original_init = Path(heldout.__file__).with_name("__init__.py")
        if original_init.is_file():
            shutil.copyfile(original_init, source / "judge/__init__.py")
        for args in (("init", "--quiet"), ("config", "user.name", "Synthetic verifier fixture"),
                     ("config", "user.email", "fixture@example.invalid"), ("add", "--all"),
                     ("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "synthetic source declarations")):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        metadata = {"task": task.name, "heldout_lock_sha256": sha((tests / "heldout.lock.json").read_bytes()),
                    "payload_sha256": sha((task / "payload.json").read_bytes()),
                    "rights_basis": "Synthetic fixtures authored by this test; no publication.",
                    "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)}
                              for name, raw in data.items()]}
        fixture = {"root": root, "source": source, "task": task, "data": data, "metadata": metadata,
                   "out": root / "prepared", "archive": root / "originals.tar.gz"}
        self.archive(fixture)
        return fixture

    def archive(self, fixture, entries=None):
        entries = list(fixture["data"].items()) if entries is None else entries
        with tarfile.open(fixture["archive"], "w:gz") as archive:
            for name, raw in entries:
                if isinstance(name, tarfile.TarInfo):
                    member = name
                else:
                    member = tarfile.TarInfo(name)
                    member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        raw = fixture["archive"].read_bytes()
        fixture["metadata"]["archive"] = {"bytes": len(raw), "sha256": sha(raw)}

    def prepare(self, fixture):
        with patch.object(packets.Fetcher, "url", side_effect=AssertionError("Local preparation must not fetch")):
            return packets.prepare(fixture["task"], fixture["metadata"], fixture["out"], fixture["archive"])

    def test_lock_only_asset_is_restored_and_real_canonical_consumer_uses_fresh_git_view(self):
        fixture = self.fixture()
        original_code = (fixture["task"] / "tests/task.py").read_bytes()
        inventory = packets.inventory(fixture["task"])
        locked = next(row for row in inventory["files"] if "lock-only" in row["path"])
        self.assertIsNone(locked["bytes"])
        self.assertEqual({row["path"] for row in inventory["source_required_files"]},
                         {"tests/task.py", "tests/guard.json"})
        with self.assertRaisesRegex(ValueError, "Held-out file not present"):
            heldout.check(fixture["task"] / "tests", require_available=True, allow_missing_references=True)
        receipt = self.prepare(fixture)
        prepared_task = Path(receipt["prepared_task"])
        self.assertEqual(prepared_task, fixture["out"] / "tasks/example")
        self.assertEqual(receipt["lock_only_files_restored"], 1)
        self.assertEqual(receipt["payload_files_staged"], 3)
        self.assertEqual(receipt["canonical_retained_check"], "available")
        for name, raw in fixture["data"].items():
            self.assertEqual((prepared_task / name).read_bytes(), raw)
            self.assertFalse((fixture["task"] / name).exists())
        self.assertEqual((prepared_task / "tests/task.py").read_bytes(), original_code)
        self.assertEqual((fixture["task"] / "tests/task.py").read_bytes(), original_code)
        self.assertEqual(heldout.check(prepared_task / "tests", require_available=True,
                                      allow_missing_references=True)["status"], "available")
        result = subprocess.run(
            [sys.executable, "-m", "judge.heldout", str(prepared_task / "tests"),
             str(fixture["root"] / "canonical-project-cache")],
            cwd=fixture["out"], env={**os.environ, "PYTHONPATH": "."}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((fixture["root"] / "canonical-project-cache").is_dir())
        original_result = subprocess.run(
            [sys.executable, "-m", "judge.heldout", str(fixture["task"] / "tests"),
             str(fixture["root"] / "must-not-create")],
            cwd=fixture["out"], env={**os.environ, "PYTHONPATH": "."}, capture_output=True, text=True)
        self.assertNotEqual(original_result.returncode, 0)
        self.assertFalse((fixture["root"] / "must-not-create").exists())
        self.assertEqual(subprocess.check_output(["git", "rev-list", "--all", "--count"],
                                                cwd=fixture["out"]).strip(), b"1")
        self.assertEqual(subprocess.check_output(["git", "remote"], cwd=fixture["out"]).strip(), b"")
        self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"],
                                                cwd=fixture["out"]).strip(), b"")
        self.assertFalse(receipt["input_acquisition_performed"])
        self.assertFalse(receipt["native_or_model_run"])

    def test_archive_missing_or_extra_assets_duplicates_and_code_overwrites_fail_without_visible_view(self):
        for attack in ("missing", "extra", "duplicate", "guard", "lock", "source", "attributes"):
            with self.subTest(attack=attack):
                fixture = self.fixture()
                entries = list(fixture["data"].items())
                if attack == "missing":
                    entries = [entry for entry in entries if "lock-only" not in entry[0]]
                elif attack == "duplicate":
                    entries.append(entries[0])
                else:
                    name = {"extra": "tests/heldout/extra.json", "guard": "tests/guard.json",
                            "lock": "tests/heldout.lock.json", "source": "tests/task.py",
                            "attributes": ".gitattributes"}[attack]
                    entries.append((name, b"not an authorized retained data member"))
                self.archive(fixture, entries)
                before = (fixture["task"] / "tests/guard.json").read_bytes()
                with self.assertRaises(ValueError):
                    self.prepare(fixture)
                self.assertFalse(fixture["out"].exists())
                self.assertFalse((fixture["task"] / "tests/heldout").exists())
                self.assertEqual((fixture["task"] / "tests/guard.json").read_bytes(), before)

    def test_corrupt_same_length_member_and_compressed_pin_mismatch_fail_closed(self):
        for attack in ("member", "lock-only-member", "archive-hash", "archive-size", "member-size"):
            with self.subTest(attack=attack):
                fixture = self.fixture()
                if attack in ("member", "lock-only-member", "member-size"):
                    entries = list(fixture["data"].items())
                    index = next(i for i, entry in enumerate(entries) if "lock-only" in entry[0]) if attack == "lock-only-member" else 0
                    name, raw = entries[index]
                    entries[index] = (name, raw + b"x" if attack == "member-size" else bytes([raw[0] ^ 1]) + raw[1:])
                    self.archive(fixture, entries)
                else:
                    fixture["metadata"]["archive"]["sha256" if attack == "archive-hash" else "bytes"] = (
                        "0" * 64 if attack == "archive-hash" else 1)
                with self.assertRaises(ValueError):
                    self.prepare(fixture)
                self.assertFalse(fixture["out"].exists())

    def test_path_escape_absolute_noncanonical_and_links_never_extract(self):
        for attack in ("../escape", "/absolute", "tests/heldout/../guard.json",
                       "tests//heldout/extra", "tests/heldout/line\nbreak", "symlink", "hardlink"):
            with self.subTest(attack=attack):
                fixture = self.fixture()
                entries = list(fixture["data"].items())
                if attack in ("symlink", "hardlink"):
                    member = tarfile.TarInfo(entries[0][0])
                    member.type = tarfile.SYMTYPE if attack == "symlink" else tarfile.LNKTYPE
                    member.linkname = "../../guard.json"
                    entries[0] = (member, b"")
                else:
                    entries.append((attack, b"no escape"))
                self.archive(fixture, entries)
                with self.assertRaises(ValueError):
                    self.prepare(fixture)
                self.assertFalse(fixture["out"].exists())
                self.assertFalse((fixture["root"] / "escape").exists())

    def test_lfs_pointer_body_rejected_even_when_original_metadata_pins_its_own_bytes(self):
        fixture = self.fixture(pointer=True)
        with self.assertRaisesRegex(ValueError, "LFS object not fetched"):
            self.prepare(fixture)
        self.assertFalse(fixture["out"].exists())

    def test_only_declared_parent_directories_are_permitted(self):
        for extra in (False, True):
            with self.subTest(undeclared_directory=extra):
                fixture = self.fixture()
                directory = tarfile.TarInfo("tests/heldout/undeclared-empty-root" if extra else "tests/heldout")
                directory.type = tarfile.DIRTYPE
                self.archive(fixture, [(directory, b""), *fixture["data"].items()])
                if extra:
                    with self.assertRaisesRegex(ValueError, "Undeclared packet directory"):
                        self.prepare(fixture)
                    self.assertFalse(fixture["out"].exists())
                else:
                    self.assertEqual(self.prepare(fixture)["canonical_retained_check"], "available")

    def test_fetch_only_inputs_remain_absent_and_acquisition_is_left_to_original_canonical_flow(self):
        fixture = self.fixture(references=True)
        with patch.object(heldout.urllib.request, "urlopen", side_effect=AssertionError("No input acquisition")):
            receipt = self.prepare(fixture)
        self.assertEqual(len(packets.inventory(fixture["task"])["files"]), 3)
        prepared = Path(receipt["prepared_task"])
        lock = heldout.check(prepared / "tests", require_available=True, allow_missing_references=True)
        self.assertEqual(len(lock["references"]), 1)
        for path in lock["references"]:
            self.assertFalse((prepared / "tests" / path).exists())
        name = next(iter(lock["references"]))
        self.archive(fixture, [*fixture["data"].items(), ("tests/" + name, b"unapproved retained input")])
        fixture["out"] = fixture["root"] / "must-not-retain-reference"
        with self.assertRaisesRegex(ValueError, "Extra packet member"):
            self.prepare(fixture)
        self.assertFalse(fixture["out"].exists())

    def test_reviewed_metadata_cannot_change_source_pins_required_closure_sizes_or_rights(self):
        for attack in ("lock", "payload", "missing", "extra-code", "duplicate", "size", "hash", "rights"):
            with self.subTest(attack=attack):
                fixture = self.fixture()
                metadata = fixture["metadata"]
                if attack in ("lock", "payload"):
                    metadata["heldout_lock_sha256" if attack == "lock" else "payload_sha256"] = "0" * 64
                elif attack == "missing":
                    metadata["files"].pop()
                elif attack == "extra-code":
                    metadata["files"].append({"path": "tests/guard.json", "bytes": 1, "sha256": "a" * 64})
                elif attack == "duplicate":
                    metadata["files"].append(copy.deepcopy(metadata["files"][0]))
                elif attack in ("size", "hash"):
                    metadata["files"][0]["bytes" if attack == "size" else "sha256"] = (
                        0 if attack == "size" else "0" * 64)
                else:
                    metadata["rights_basis"] = ""
                with self.assertRaises(ValueError):
                    self.prepare(fixture)
                self.assertFalse(fixture["out"].exists())

    def test_missing_protected_source_stays_a_code_gap_not_a_data_packet_repair(self):
        fixture = self.fixture()
        (fixture["task"] / "tests/guard.json").unlink()
        with self.assertRaisesRegex(ValueError, "Required protected source is missing/corrupt"):
            self.prepare(fixture)
        self.assertFalse(fixture["out"].exists())

    def test_unavailable_lock_and_nonpublic_or_temporary_urls_do_not_create_readiness(self):
        fixture = self.fixture()
        lock_path = fixture["task"] / "tests/heldout.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["status"] = "unavailable"
        lock_path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "lock is unavailable"):
            self.prepare(fixture)
        for url in ("http://fixture.invalid/archive", "https://fixture.invalid/archive?temporary=1"):
            fixture = self.fixture()
            fixture["metadata"]["archive"]["url"] = url
            with self.assertRaisesRegex(ValueError, "stable credential-free"):
                self.prepare(fixture)
            self.assertFalse(fixture["out"].exists())

    def test_source_workspace_or_existing_output_is_never_overwritten(self):
        for attack in ("source", "existing", "packet-in-source"):
            with self.subTest(attack=attack):
                fixture = self.fixture()
                if attack == "source":
                    fixture["out"] = fixture["source"] / "not-a-verifier-view"
                elif attack == "existing":
                    fixture["out"].mkdir()
                    (fixture["out"] / "live").write_text("preserved")
                else:
                    target = fixture["source"] / "must-not-track-packet.tar.gz"
                    shutil.copyfile(fixture["archive"], target)
                    fixture["archive"] = target
                with self.assertRaises(ValueError):
                    self.prepare(fixture)
                if attack == "existing":
                    self.assertEqual((fixture["out"] / "live").read_text(), "preserved")

    def test_serde_restricted_goldens_keep_the_separate_pristine_producer_route(self):
        fixture = self.fixture(name=packets.SERDE)
        with self.assertRaisesRegex(ValueError, "restricted six goldens"):
            self.prepare(fixture)
        self.assertFalse(fixture["out"].exists())

    def test_no_implicit_url_and_only_existing_pinned_fetcher_for_explicit_public_record(self):
        fixture = self.fixture()
        with patch.object(packets.Fetcher, "url", side_effect=AssertionError("No implicit fetch")):
            with self.assertRaisesRegex(ValueError, "No approved public archive URL"):
                packets.prepare(fixture["task"], fixture["metadata"], fixture["out"])
        fixture["metadata"]["archive"]["url"] = "https://fixture.invalid/versioned-verifier.tar.gz"
        with patch.object(packets.Fetcher, "url", return_value=fixture["archive"]) as fetch:
            packets.prepare(fixture["task"], fixture["metadata"], fixture["out"])
        fetch.assert_called_once_with(fixture["metadata"]["archive"])

    def test_cli_inventory_is_metadata_only_and_duplicate_metadata_keys_are_rejected(self):
        fixture = self.fixture()
        result = subprocess.run(
            [sys.executable, "-m", "tools.prepare_verifier_assets", "--task", str(fixture["task"]), "--inventory"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["files"]), 4)
        self.assertFalse(fixture["out"].exists())
        metadata = fixture["root"] / "duplicate.json"
        metadata.write_text('{"task":"example","task":"other"}')
        result = subprocess.run(
            [sys.executable, "-m", "tools.prepare_verifier_assets", "--task", str(fixture["task"]),
             "--metadata", str(metadata), "--archive", str(fixture["archive"]), "--out", str(fixture["out"])],
            cwd=ROOT, env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate reviewed metadata key", result.stderr)
        self.assertFalse(fixture["out"].exists())

    def test_documented_module_cli_prepares_the_same_canonical_view_from_local_packet_without_fetch(self):
        fixture = self.fixture()
        metadata = fixture["root"] / "reviewed-release-metadata.json"
        metadata.write_text(json.dumps(fixture["metadata"]))
        result = subprocess.run(
            [sys.executable, "-m", "tools.prepare_verifier_assets", "--task", str(fixture["task"]),
             "--metadata", str(metadata), "--archive", str(fixture["archive"]), "--out", str(fixture["out"])],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["prepared_task"], str(fixture["out"] / "tasks/example"))
        self.assertEqual(receipt["canonical_retained_check"], "available")
        self.assertTrue((fixture["out"] / ".git").is_dir())


ROOT = Path(packets.__file__).resolve().parents[1]

if __name__ == "__main__":
    unittest.main()
