"""Task guards grow with the canonical catalog without losing coverage."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from judge.task_catalog import task_paths

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import check_tasks
from tools.tests.task_fixture import without_private_payload


class TaskCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "datasets").mkdir()
        (self.root / "tasks").mkdir()

    def catalog(self, names):
        (self.root / "datasets/all.json").write_text(json.dumps({"tasks": names}))

    def add_task(self, name):
        path = self.root / "tasks" / name
        path.mkdir()
        (path / "task.json").write_text("{}")

    def test_new_catalog_task_is_covered_without_changing_a_count(self):
        self.add_task("existing")
        self.catalog(["existing"])
        self.assertEqual([path.name for path in task_paths(self.root)], ["existing"])
        self.add_task("new-task")
        self.catalog(["new-task", "existing"])
        self.assertEqual([path.name for path in task_paths(self.root)], ["existing", "new-task"])

    def test_missing_catalog_task_and_unknown_directory_fail(self):
        self.add_task("existing")
        self.catalog(["existing", "missing"])
        with self.assertRaisesRegex(ValueError, "missing=.*missing"):
            task_paths(self.root)
        self.catalog(["existing"])
        self.add_task("unlisted")
        with self.assertRaisesRegex(ValueError, "unknown=.*unlisted"):
            task_paths(self.root)

    def test_empty_duplicate_unsafe_and_incomplete_catalogs_fail(self):
        for names in ([], ["same", "same"], ["../escape"], ["/absolute"], ["."], [42]):
            with self.subTest(names=names):
                self.catalog(names)
                with self.assertRaisesRegex(ValueError, "unique, nonempty"):
                    task_paths(self.root)
        self.add_task("incomplete")
        (self.root / "tasks/incomplete/task.json").unlink()
        self.catalog(["incomplete"])
        with self.assertRaisesRegex(ValueError, "missing task.json"):
            task_paths(self.root)

    def test_verifier_reference_sidecar_requires_a_matching_content_lock(self):
        task = self.root / "tasks/sqlglot-mozilla-transpile"
        shutil.copytree(TOOLS.parent / "tasks/sqlglot-mozilla-transpile", task)
        without_private_payload(task)
        sidecar = task / "tests/baseline-outputs.json"
        sidecar.write_text("[]\n")
        lock_path = task / "tests/heldout.lock.json"
        lock = json.loads(lock_path.read_text())
        self.assertNotIn("baseline-outputs.json", lock["files"])
        with self.assertRaisesRegex(AssertionError, "baseline-outputs metadata must be content-locked"):
            check_tasks.check_task(task)

        digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
        lock["files"]["baseline-outputs.json"] = digest
        lock_path.write_text(json.dumps(lock))
        provenance_path = task / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["current_files_sha256"]["tests/heldout.lock.json"] = hashlib.sha256(
            lock_path.read_bytes()).hexdigest()
        provenance["current_files_sha256"]["tests/baseline-outputs.json"] = digest
        provenance_path.write_text(json.dumps(provenance))
        self.assertEqual(check_tasks.check_task(task)["task"], task.name)
        self.assertFalse(sidecar.is_relative_to(task / "environment"))

        sidecar.write_text("[{}]\n")
        with self.assertRaisesRegex(AssertionError, "baseline-outputs metadata must be content-locked"):
            check_tasks.check_task(task)
