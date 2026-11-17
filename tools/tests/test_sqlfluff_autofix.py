"""Unscored fixtures for the SQLFluff authoring and filesystem contracts."""
import copy
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sqlfluff_author", ROOT / "tools/author_sqlfluff_autofix.py")
author = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(author)
DRIVER = importlib.util.spec_from_file_location(
    "sqlfluff_autofix_driver",
    ROOT / "tasks/sqlfluff-project-lint/environment/workload/driver/autofix.py")
autofix = importlib.util.module_from_spec(DRIVER)
DRIVER.loader.exec_module(autofix)


class AutoFixContractTest(unittest.TestCase):
    def test_upstream_expectations_include_all_protected_autofix_cases(self):
        task = ROOT / "tasks/sqlfluff-project-lint"
        release = json.loads((task / "release.json").read_text())
        self.assertIn("/app/workload/driver/test_autofix_contract.py",
                      release["upstream_test_command"])
        tree = ast.parse((task / "environment/workload/driver/test_autofix_contract.py").read_text())
        cases = 0
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            count = 1
            for decorator in node.decorator_list:
                self.assertIsInstance(decorator, ast.Call)
                self.assertEqual(ast.unparse(decorator.func), "pytest.mark.parametrize")
                count *= len(ast.literal_eval(decorator.args[1]))
            cases += count
        self.assertEqual(cases, 9)
        expected = json.loads((task / "tests/upstream-expectations.json").read_text())
        self.assertEqual(expected["suites"]["upstream"], {
            "passed": 11408 + cases, "collected": 14038 + cases,
            "skipped": 2630, "failed": 0,
        })
        self.assertEqual(len(set(expected["skipped_nodeids"])), 2630)

    def test_directory_link_counts_are_portable_but_still_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.sql").write_bytes(b"select 1")
            actor = {"uid": os.geteuid(), "gid": os.getegid()}
            before = autofix.snapshot(root)
            baseline = autofix.observations(before, before, set(), actor)
            other_filesystem = copy.deepcopy(before)
            other_filesystem["."]["nlink"] += 1
            self.assertEqual(
                baseline,
                autofix.observations(other_filesystem, other_filesystem, set(), actor))
            self.assertNotIn("nlink", baseline["files"]["."])
            self.assertEqual(baseline["files"]["a.sql"]["nlink"], before["a.sql"]["nlink"])
            for name in (".", "a.sql"):
                after = copy.deepcopy(before)
                after[name]["nlink"] += 1
                result = autofix.observations(before, after, set(), actor)
                self.assertEqual(result["filesystem_violations"],
                                 [name + ": protected-metadata-changed"])

    def test_each_source_uses_its_declared_dialect(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "a.sql").write_bytes(b"select 1")
            ctx = SimpleNamespace(
                scratch=root, build=root / "build", workload=root / "workload",
                copy_project=lambda name, digest: project)
            data = {"project": "fixture", "inventory_sha256": "a" * 64,
                    "targets": ["a.sql"], "timeout": 180}
            for dialect in ("postgres", "bigquery", "snowflake"):
                with self.subTest(dialect=dialect):
                    step, = autofix.prepare(SimpleNamespace(data={**data, "dialect": dialect}), ctx)
                    self.assertIn("--dialect=" + dialect, step.argv)
            default, = autofix.prepare(SimpleNamespace(data=data), ctx)
            self.assertIn("--dialect=postgres", default.argv)
            with self.assertRaisesRegex(ValueError, "dialect"):
                autofix.prepare(SimpleNamespace(data={**data, "dialect": "--help"}), ctx)

    def test_every_final_byte_and_non_target_metadata_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.sql").write_bytes(b"select 1")
            (root / "LICENSE").write_bytes(b"unit-test notice")
            before = autofix.snapshot(root)
            actor = {"uid": os.geteuid(), "gid": os.getegid()}
            for name, key, value in (("LICENSE", "mode", 0), ("a.sql", "mtime_ns", 0),
                                     (".", "inode", 0), ("LICENSE", "content_base64", "AAAA")):
                after = copy.deepcopy(before)
                after[name][key] = value
                result = autofix.observations(before, after, {"a.sql"}, actor)
                self.assertTrue(result["filesystem_violations"], (name, key))
            after = copy.deepcopy(before)
            after["a.sql"].update(sha256="different", content_base64="AAAA", inode=999)
            result = autofix.observations(before, after, {"a.sql"}, actor)
            self.assertEqual(result["filesystem_violations"], [])
            self.assertEqual(result["files"]["a.sql"]["content_base64"], "AAAA")
            after["a.sql"]["uid"] += 1
            self.assertTrue(autofix.observations(before, after, {"a.sql"}, actor)["filesystem_violations"])

    def test_commit_failure_restores_every_prior_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination, staged = root / "task", root / "stage"
            destination.mkdir()
            staged.mkdir()
            (destination / "a").write_bytes(b"original")
            (staged / "a").write_bytes(b"updated")
            (staged / "new").mkdir()
            (staged / "new/b").write_bytes(b"new")
            replace = os.replace
            calls = 0

            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected commit failure")
                return replace(source, target)

            with patch.object(author.os, "replace", fail_second):
                with self.assertRaises(OSError):
                    author.commit_staged(staged, destination)
            self.assertEqual((destination / "a").read_bytes(), b"original")
            self.assertEqual(sorted(p.name for p in destination.iterdir()), ["a"])

    def test_incomplete_reference_never_reaches_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            originals = root / "originals"
            originals.mkdir()
            files = {"a.sql": b"select 1", "LICENSE": b"unit-test notice"}
            for name, data in files.items():
                (originals / name).write_bytes(data)
            plan = {"repository": "fixture/repository", "revision": "a" * 40,
                    "targets": ["a.sql"], "notices": ["LICENSE"], "files": []}
            for name, data in files.items():
                row = {"path": name, "bytes": len(data), "sha256": author.digest(data)}
                row["url"] = author.original_url(plan, row)
                plan["files"].append(row)
            plan_path, reference = root / "plan.json", root / "reference.json"
            plan_path.write_text(json.dumps(plan))
            reference.write_text(json.dumps({"format": "sqlfluff-project-autofix-v1",
                                             "filesystem_violations": [], "files": {"a.sql": {}}}))
            receipt = root / "reference-provenance.json"
            receipt.write_text("{}")
            with patch.object(author, "commit_staged") as commit:
                with self.assertRaisesRegex(ValueError, "schema"):
                    author.author("public", plan_path, originals, reference, receipt)
                commit.assert_not_called()

    def test_generated_or_traversing_origins_fail_closed(self):
        plan = {"repository": "fixture/repository", "revision": "a" * 40}
        for name in ("../outside.sql", "./a.sql", "/a.sql"):
            with self.assertRaises(ValueError):
                author.original_url(plan, {"path": name})
        plan["revision"] = "main"
        with self.assertRaises(ValueError):
            author.original_url(plan, {"path": "a.sql"})


if __name__ == "__main__":
    unittest.main()
