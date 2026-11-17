import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from judge import policy, source_policy, upstream


class TestAdditionPolicy(unittest.TestCase):
    def compare(self, old, new):
        upstream.compare({"upstream": {"passed": True, "outcomes": old}},
                         {"upstream": {"passed": True, "outcomes": new}}, "anydoc-markdown")

    def test_added_passing_skipped_and_xfailed_tests_are_allowed(self):
        old = {"original": "passed", "original-skip": "skipped"}
        for status in ("passed", "skipped", "xfail", {"skip": "new reason"},
                       {"setup": "passed", "call": "skipped", "teardown": "passed"}):
            self.compare(old, {**old, "new": status})

    def test_added_failure_error_and_original_outcome_changes_are_rejected(self):
        old = {"original": "passed"}
        for new in ({**old, "new": "failed"}, {**old, "new": "error"},
                    {**old, "new": {"collection": "deselected"}},
                    {"original": "skipped"}, {"renamed": "passed"}, {}):
            with self.subTest(new=new), self.assertRaises(ValueError):
                self.compare(old, new)

    def test_original_known_failure_must_remain_exact(self):
        self.compare({"historical": "failed"}, {"historical": "failed", "new": "passed"})
        with self.assertRaises(ValueError):
            self.compare({"historical": "failed"}, {"historical": "passed"})

    def test_original_xfail_and_deselection_cannot_change(self):
        old = {"x": {"call": "skipped", "call_wasxfail": "original reason"},
               "d": {"collection": "deselected"}}
        self.compare(old, {**old, "new": "passed"})
        with self.assertRaises(ValueError):
            self.compare(old, {**old, "x": {"call": "skipped"}})

    def test_structured_pytest_rust_and_babel_ledgers_allow_additions(self):
        reports = [
            ({"collected": 1, "outcomes": {"old": {"call": "passed"}}},
             {"collected": 2, "outcomes": {"old": {"call": "passed"}, "new": {"call": "passed"}}}),
            ({"tests": [{"target": "lib", "name": "old", "status": "ok"}], "summaries": [1]},
             {"tests": [{"target": "lib", "name": "old", "status": "ok"},
                        {"target": "lib", "name": "new", "status": "ignored"}], "summaries": [2]}),
            ({"outcomes": [{"file": "a", "tests": [{"name": "old", "status": "passed"}]}], "counts": {}},
             {"outcomes": [{"file": "a", "tests": [{"name": "old", "status": "passed"},
                                                   {"name": "new", "status": "pending"}]}], "counts": {}}),
        ]
        for old, new in reports:
            self.compare(old, new)

    def test_original_identity_multiplicity_cannot_change(self):
        old = {"tests": [{"target": "lib", "name": "old", "status": "ok"}]}
        new = copy.deepcopy(old)
        new["tests"].append(dict(new["tests"][0]))
        with self.assertRaises(ValueError):
            self.compare(old, new)

    def test_new_fixture_and_additive_test_source_are_bound_but_old_snapshot_is_protected(self):
        rules = {"source_roots": ["src"], "allowed_extensions": [".py"], "protected_paths": [],
                 "max_source_files": 10, "max_source_bytes": 10000}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pristine, candidate = root / "old", root / "new"
            (pristine / "src").mkdir(parents=True)
            (pristine / "src/library.py").write_text("def f(): return 1\n")
            (pristine / "tests/snapshots").mkdir(parents=True)
            (pristine / "tests/test_original.py").write_text("def test_original():\n    assert True\n")
            (pristine / "tests/snapshots/old.txt").write_text("original expected output\n")
            shutil.copytree(pristine, candidate)
            baseline = policy.envelope(pristine, pristine, rules)
            self.assertEqual(policy.source_digest(baseline), source_policy.digest(baseline["files"]))
            with (candidate / "tests/test_original.py").open("a") as stream:
                stream.write("\ndef test_new():\n    assert True\n")
            (candidate / "tests/snapshots/new.bin").write_bytes(b"\0new fixture")
            admitted = policy.envelope(candidate, pristine, rules)
            self.assertNotEqual(policy.source_digest(admitted), policy.source_digest(baseline))
            (candidate / "tests/snapshots/new.bin").write_bytes(b"different")
            self.assertNotEqual(policy.source_digest(admitted),
                                policy.source_digest(policy.envelope(candidate, pristine, rules)))
            (candidate / "tests/snapshots/old.txt").write_text("changed")
            with self.assertRaisesRegex(ValueError, "fixture/snapshot"):
                policy.envelope(candidate, pristine, rules)

    def test_existing_test_source_cannot_be_edited_or_renamed(self):
        self.assertTrue(policy.additions_only("old\n", "old\nnew\n"))
        self.assertFalse(policy.additions_only("old\n", "renamed\n"))

