"""Frozen packages retain registered canonical guards without image refreezes."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import check_tasks
import update_catalog
from tools.tests.task_fixture import without_private_payload

LEGACY = Path(__file__).parent / "fixtures/payload_guards/url-recipes-v1.py"


class GuardVersionTests(unittest.TestCase):
    def test_old_canonical_url_only_frozen_package_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "tasks/sqlglot-mozilla-transpile"
            shutil.copytree(TOOLS.parent / "tasks/sqlglot-mozilla-transpile", task)
            (root / "tools/runner").mkdir(parents=True)
            for name in ("payload_check.py", "payload_guard_versions.json"):
                shutil.copyfile(TOOLS / name, root / "tools" / name)
            shutil.copyfile(TOOLS / "runner/runner.py", root / "tools/runner/runner.py")
            shutil.copyfile(TOOLS.parent / "README.md", root / "README.md")
            (root / "datasets").mkdir()
            guard = task / "environment/payload_check.py"
            shutil.copyfile(LEGACY, guard)
            frozen = guard.read_bytes()
            without_private_payload(task)
            with patch.object(check_tasks, "ROOT", root), patch.object(update_catalog, "ROOT", root):
                # Construct a correctly frozen legacy fixture, not a stale hash binding.
                for path, text in update_catalog.generated().items():
                    path.write_text(text)
                result = check_tasks.check_task(task)
            self.assertTrue(result["shell_complete"])
            self.assertEqual(result["guard_version"]["id"], "url-recipes-v1")
            self.assertTrue(result["guard_version"]["older_canonical"])
            self.assertIn("(older canonical)", result["guard_version"]["message"])
            self.assertEqual(guard.read_bytes(), frozen)

    def test_old_canonical_guard_cannot_handle_git_history(self):
        with self.assertRaisesRegex(AssertionError, "does not support payload kinds"):
            check_tasks.guard_version(LEGACY, {"files": [{"kind": "file"}, {"kind": "git-history"}]})

    def test_modified_guard_is_not_a_released_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            guard = Path(temporary) / "payload_check.py"
            guard.write_bytes(LEGACY.read_bytes() + b"\n# modified after release\n")
            with self.assertRaisesRegex(AssertionError, "unknown canonical hash"):
                check_tasks.guard_version(guard, {"files": [{}]})

    def test_current_guard_supports_all_current_payload_kinds(self):
        result = check_tasks.guard_version(TOOLS / "payload_check.py",
                                          {"files": [{"kind": "file"}, {"kind": "git-history"}]})
        self.assertEqual(result["id"], "repo-lfs-public-gzip-v4")
        self.assertFalse(result["older_canonical"])

    def test_unknown_payload_kind_and_unregistered_current_fail_closed(self):
        with self.assertRaisesRegex(AssertionError, "does not support payload kinds"):
            check_tasks.guard_version(TOOLS / "payload_check.py", {"files": [{"kind": "future-unknown"}]})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tools").mkdir()
            shutil.copyfile(TOOLS / "payload_guard_versions.json", root / "tools/payload_guard_versions.json")
            (root / "tools/payload_check.py").write_text("# not registered\n")
            with patch.object(check_tasks, "ROOT", root), self.assertRaisesRegex(AssertionError, "unregistered"):
                check_tasks.guard_version(LEGACY, {"files": [{}]})

    def test_known_broken_portable_cli_guard_is_revoked_for_every_kind(self):
        registry = json.loads((TOOLS / "payload_guard_versions.json").read_text())
        current = next(row for row in registry["versions"] if row["id"] == registry["current"])
        broken = next(row for row in registry["versions"] if row["id"] == "git-history-v1-portable")
        for kind in ("file", "git-history"):
            with patch.object(check_tasks, "sha", side_effect=[current["sha256"], broken["sha256"]]):
                with self.assertRaisesRegex(AssertionError, "release revoked"):
                    check_tasks.guard_version(LEGACY, {"files": [{"kind": kind}]})

    def test_original_git_history_release_remains_acceptable(self):
        registry = json.loads((TOOLS / "payload_guard_versions.json").read_text())
        current = next(row for row in registry["versions"] if row["id"] == registry["current"])
        original = next(row for row in registry["versions"] if row["id"] == "git-history-v1")
        with patch.object(check_tasks, "sha", side_effect=[current["sha256"], original["sha256"]]):
            result = check_tasks.guard_version(LEGACY, {"files": [{"kind": "git-history"}]})
        self.assertEqual(result["id"], "git-history-v1")
        self.assertTrue(result["older_canonical"])

    def test_broken_recipe_releases_are_rejected_even_inside_concat(self):
        registry = json.loads((TOOLS / "payload_guard_versions.json").read_text())
        current = next(row for row in registry["versions"] if row["id"] == registry["current"])
        for identity in ("git-history-v1", "git-history-v1-public-cli"):
            broken = next(row for row in registry["versions"] if row["id"] == identity)
            for recipe in ({"kind": "png-rgb24"},
                           {"kind": "concat", "parts": [{"kind": "png-rgb24"}]}):
                with self.subTest(identity=identity, recipe=recipe):
                    with patch.object(check_tasks, "sha", side_effect=[current["sha256"], broken["sha256"]]):
                        with self.assertRaisesRegex(AssertionError, "release revoked for recipes"):
                            check_tasks.guard_version(LEGACY, {"files": [{"fetch": recipe}]})
