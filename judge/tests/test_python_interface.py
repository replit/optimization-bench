"""Narrow Python surfaces, with protected subclass/plugin escape hatches."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from judge import interfaces, policy, source_policy

ROOT = Path(__file__).resolve().parents[2]
DOC = "https://example.org/api"


class PythonInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline"
        self.candidate = self.root / "candidate"
        self.config = {"documentation": [DOC], "documented_api": [], "extension_points": []}

    def surface(self, root, files, selection=None):
        for name, text in files.items():
            path = root / "sample" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        script = (
            "import json,sys; from pathlib import Path; "
            "from judge.interface_probe import python_surface; "
            "print(json.dumps(python_surface(Path(sys.argv[1]), ['sample'], "
            "json.loads(sys.argv[2]), json.loads(sys.argv[3]))))")
        return json.loads(subprocess.check_output(
            [sys.executable, "-B", "-c", script, str(root), json.dumps(self.config),
             json.dumps(selection)], cwd=ROOT, text=True))

    def test_top_all_excludes_unadvertised_names_but_keeps_reexported_fields(self):
        files = {"__init__.py": "__all__=['API']\nfrom ._impl import API\n"
                 "def helper(value): return value\n",
                 "_impl.py": "class API:\n public=1\n _private=2\n def run(self, value): return value\n"}
        before = self.surface(self.baseline, files)
        self.assertNotIn("sample:helper", before)
        self.assertIn("sample:API.public", before)
        after_files = {**files, "_impl.py": files["_impl.py"].replace("_private", "_changed")}
        after = self.surface(self.candidate, after_files, before)
        self.assertTrue(interfaces.compare(before, after)["passed"])
        removed_files = {**files, "_impl.py": files["_impl.py"].replace(" public=1", " _public=1")}
        removed = self.surface(self.candidate, removed_files, before)
        self.assertFalse(interfaces.compare(before, removed)["passed"])

    def test_explicit_foreign_reexport_is_not_silently_dropped(self):
        files = {"__init__.py": "__all__=['Counter']\nfrom collections import Counter\n"}
        before = self.surface(self.baseline, files)
        self.assertEqual(before["sample:Counter"]["external_origin"], "collections:Counter")
        after = self.surface(self.candidate, {"__init__.py": "__all__=[]\nfrom collections import Counter\n"}, before)
        self.assertFalse(interfaces.compare(before, after)["passed"])

    def test_python_alias_relationship_and_default_star_exports_are_preserved(self):
        files = {"__init__.py": "__all__=['API','Alias']\nclass API: pass\nAlias=API\n"}
        before = self.surface(self.baseline, files)
        unchanged = self.surface(self.candidate, {"__init__.py": "class API: pass\nAlias=API\n"}, before)
        self.assertTrue(interfaces.compare(before, unchanged)["passed"])
        split = self.surface(self.candidate, {"__init__.py": "__all__=['API','Alias']\nclass API: pass\nclass Alias: pass\n"}, before)
        self.assertFalse(interfaces.compare(before, split)["passed"])

    def compare(self, before, after):
        old = self.surface(self.baseline, before)
        new = self.surface(self.candidate, after, selection=old)
        return old, new, interfaces.compare(old, new)

    def test_stdlib_and_dependency_reexports_are_ignored(self):
        dependency = self.baseline / "dependency"
        dependency.mkdir(parents=True)
        (dependency / "__init__.py").write_text("class External:\n pass\n")
        before = {"__init__.py": "from pathlib import Path\nfrom json import dumps\n"
                  "from dependency import External\n"
                  "import os\nfrom collections import Counter\n"
                  "def keep(x): return x\n"}
        after = {"__init__.py": "def keep(x): return x\n"}
        old, _, report = self.compare(before, after)
        self.assertEqual(set(old), {"sample", "sample:keep"})
        self.assertTrue(report["passed"])

    def test_top_level_own_reexport_stays_protected(self):
        before = {"__init__.py": "from .internal import keep\n",
                  "internal.py": "def keep(x): return x\n"}
        after = {"__init__.py": "", "internal.py": before["internal.py"]}
        _, _, report = self.compare(before, after)
        self.assertEqual(report["removed"], ["sample:internal", "sample:keep"])

    def test_discovery_subpackage_imports_do_not_pollute_top_namespace(self):
        before = {"__init__.py": "def keep(x): return x\n",
                  "internal/__init__.py": "",
                  "internal/nested.py": "def implementation(): pass\n"}
        old, _, report = self.compare(before, {"__init__.py": before["__init__.py"]})
        self.assertEqual(set(old), {"sample", "sample:keep"})
        self.assertTrue(report["passed"])

    def test_extension_underscore_signature_change_is_rejected_exactly(self):
        self.config["extension_points"] = [{"name": "sample.internal:Base", "documentation": DOC}]
        before = {"__init__.py": "", "internal.py":
                  "class Base:\n def _eval(self, x): return x\n"
                  " @classmethod\n def _hook(cls, x=1): return x\n"}
        after = {**before, "internal.py": before["internal.py"].replace("_eval(self, x)", "_eval(self, x, y=None)")}
        old, _, report = self.compare(before, after)
        self.assertTrue(old["sample.internal:Base._eval"]["strict_signature"])
        self.assertNotIn("sample.internal:Base.__str__", old)
        self.assertEqual(report["changed"], ["sample.internal:Base._eval"])

    def test_extension_hook_removal(self):
        self.config["extension_points"] = [{"name": "sample.internal:hook", "documentation": DOC}]
        before = {"__init__.py": "", "internal.py": "def hook(x): return x\n"}
        _, _, report = self.compare(before, {**before, "internal.py": ""})
        self.assertEqual(report["removed"], ["sample.internal:hook"])

    def test_extension_classmethod_and_inherited_hook_changes(self):
        self.config["extension_points"] = [{"name": "sample.internal:Base", "documentation": DOC}]
        before = {"__init__.py": "", "internal.py":
                  "class Parent:\n def _eval(self, x): return x\n"
                  "class Base(Parent):\n @classmethod\n def _hook(cls, x=1): return x\n"}
        after = {**before, "internal.py": before["internal.py"].replace("_hook(cls, x=1)", "_hook(cls, x=2)")
                 .replace("_eval(self, x)", "_eval(self, renamed)")}
        _, _, report = self.compare(before, after)
        self.assertEqual(report["changed"], ["sample.internal:Base._eval", "sample.internal:Base._hook"])

    def test_documented_api_removal_is_rejected(self):
        self.config["documented_api"] = [{"name": "sample.internal:visible", "documentation": DOC}]
        before = {"__init__.py": "", "internal.py": "def visible(x): return x\n"}
        _, _, report = self.compare(before, {**before, "internal.py": ""})
        self.assertEqual(report["removed"], ["sample.internal:visible"])

    def test_autodoc_members_are_owned_public_callables_only(self):
        self.config["documented_api"] = [{"name": "sample.internal:*", "documentation": DOC}]
        before = {"__init__.py": "", "internal.py":
                  "from pathlib import Path\nfrom .other import imported\n"
                  "def visible(x): return x\n"
                  "def _private(x): return x\n", "other.py": "def imported(): pass\n"}
        old, _, report = self.compare(before, before)
        self.assertEqual(set(old), {"sample", "sample.internal:visible"})
        self.assertTrue(report["passed"])

    def test_private_module_internal_change_and_removal_allowed(self):
        before = {"__init__.py": "def keep(x): return x\n",
                  "internal.py": "def incidental(x): return x\n"
                  "def _private(x): return x\n"}
        # No private/internal import is required on the candidate side.
        after = {"__init__.py": before["__init__.py"], "internal.py": "raise RuntimeError('private')\n"}
        old, _, report = self.compare(before, after)
        self.assertNotIn("sample.internal:incidental", old)
        self.assertTrue(report["passed"])

    def test_public_module_all_and_private_module_exclusion(self):
        before = {"__init__.py": "", "public.py":
                  "__all__ = ['visible']\ndef visible(x): return x\n"
                  "def incidental(x): return x\n",
                  "_private.py": "__all__=['private']\ndef private(): pass\n"}
        after = {**before, "public.py": before["public.py"].replace("['visible']", "[]")}
        old, _, report = self.compare(before, after)
        self.assertNotIn("sample._private:private", old)
        self.assertNotIn("sample.public:incidental", old)
        self.assertEqual(report["removed"], ["sample.public:visible"])

    def test_judge_uses_trusted_metadata_and_pristine_selection(self):
        task = self.root / "task"
        (task / "tests").mkdir(parents=True)
        (task / "task.json").write_text(json.dumps({"interface": self.config}))
        (task / "tests/judge.json").write_text(json.dumps({"public_interface": {
            "kind": "python", "root": "runtime", "packages": ["sample"]}}))
        calls = []
        old = {"sample:keep": {"kind": "callable", "signature": "(x)", "scopes": ["top"],
                                "strict_signature": False}}
        def probe(root, config, image, cpuset):
            calls.append(config)
            return old
        with patch.object(interfaces, "docker_surface", side_effect=probe):
            self.assertTrue(interfaces.check(task, self.baseline, self.candidate, "unused", "0")["passed"])
        self.assertEqual(calls[0]["interface"], self.config)
        self.assertNotIn("_pristine_selection", calls[0])
        self.assertEqual(calls[1]["_pristine_selection"], old)

    def test_task_metadata_cannot_be_admitted_as_an_agent_edit(self):
        for name in ("sqlglot-mozilla-transpile", "numpy-quantile"):
            task = ROOT / "tasks" / name
            config = json.loads((task / "release.json").read_text())["source_policy"]
            with self.subTest(task=name):
                self.assertFalse(source_policy.allowed("task.json", config))
                pristine, candidate = self.root / name / "before", self.root / name / "after"
                pristine.mkdir(parents=True)
                candidate.mkdir(parents=True)
                (pristine / "task.json").write_bytes((task / "task.json").read_bytes())
                metadata = json.loads((task / "task.json").read_text())
                metadata["interface"]["extension_points"] = []
                metadata["interface"]["documented_api"] = []
                metadata["interface"]["documentation"] = [DOC]
                (candidate / "task.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "Protected file changed: task.json"):
                    policy.envelope(candidate, pristine, config)

    def test_metadata_requires_docs_and_in_package_references(self):
        task = self.root / "task"
        task.mkdir()
        for name in ("stdlib:Path", "sample.internal:*", "sample:bad-name"):
            self.config["extension_points"] = [{"name": name, "documentation": DOC}]
            (task / "task.json").write_text(json.dumps({"interface": self.config}))
            with self.subTest(name=name), self.assertRaises(ValueError):
                interfaces.python_interface_config(task, ["sample"])

    def test_real_task_metadata_validates(self):
        for name, package in (("sqlglot-mozilla-transpile", "sqlglot"),
                              ("numpy-quantile", "numpy")):
            with self.subTest(task=name):
                config = interfaces.python_interface_config(ROOT / "tasks" / name, [package])
                self.assertTrue(config["documentation"])


if __name__ == "__main__":
    unittest.main()
