import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from judge import native_surface as surface


class InstalledTemplateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base, self.candidate = self.root / "base", self.root / "candidate"
        self.header = ("namespace demo { struct Value { int x; }; struct Box { "
                       "template<class T> const T &Cast() const { return *reinterpret_cast<const T*>(this); }"
                       "}; }\n")
        for root in (self.base, self.candidate):
            (root / "include").mkdir(parents=True)
            (root / "include/api.hpp").write_text(self.header)
        source = self.root / "source.cpp"
        source.write_text('#include "api.hpp"\nauto &call(const demo::Box &x) { return x.Cast<demo::Value>(); }\n')
        obj = self.root / "source.o"
        subprocess.run(["c++", "-std=c++17", "-O0", "-fno-inline", "-I" + str(self.base / "include"),
                        "-c", str(source), "-o", str(obj)], check=True, capture_output=True)
        rows = subprocess.check_output(["nm", "-g", "--defined-only", str(obj)], text=True)
        self.symbol = next(row.split()[-1] for row in rows.splitlines() if "Cast" in row)
        self.key = "libfixture.so:" + self.symbol

    def project(self, kind="W"):
        return surface.project_removed({self.key: kind}, {}, self.base, self.candidate, None, None)

    def test_public_template_is_defined_independently_in_both_consumers(self):
        remaining, proof = self.project()
        self.assertEqual(remaining, {})
        for arm in ("baseline", "candidate"):
            self.assertEqual(proof["ignored"][self.key]["arms"][arm]["defined_symbol"], self.symbol)
            self.assertFalse(proof["ignored"][self.key]["arms"][arm]["linked_library"])

    def test_strong_and_non_template_weak_exports_remain_protected(self):
        self.assertEqual(self.project("T")[0], {self.key: "T"})
        values = {"libfixture.so:_ZN4demo8ordinaryEv": "W", "libfixture.so:plain_c": "W"}
        self.assertEqual(surface.project_removed(values, {}, self.base, self.candidate, None, None)[0], values)

    def test_declaration_without_definition_remains_protected(self):
        (self.candidate / "include/api.hpp").write_text(
            "namespace demo { struct Value { int x; }; struct Box { template<class T> const T &Cast() const; }; }\n")
        self.assertEqual(self.project()[0], {self.key: "W"})

    def test_extern_template_library_dependency_remains_protected(self):
        (self.candidate / "include/api.hpp").write_text(
            self.header + "namespace demo { extern template const Value &Box::Cast<Value>() const; }\n")
        self.assertEqual(self.project()[0], {self.key: "W"})

    def test_missing_installed_headers_remains_protected(self):
        (self.candidate / "include/api.hpp").unlink()
        self.assertEqual(self.project()[0], {self.key: "W"})

    def test_native_consumer_cannot_read_the_project_or_library(self):
        with patch.object(surface.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(ValueError):
                surface.compile_definition(self.base, "source", self.symbol, "image", "0-7")
        command = run.call_args.args[0]
        self.assertIn(str((self.base / "include").resolve()) + ":/installed:ro", command)
        self.assertNotIn(str(self.base.resolve()) + ":/candidate:ro", command)
        self.assertIn("-I/installed", command)


if __name__ == "__main__":
    unittest.main()
