"""Modify-only single-file Harbor artifacts never broaden a root '.' policy."""
from pathlib import Path
import tempfile
import unittest
from judge import policy


class FileArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pristine = self.root / "pristine"
        self.pristine.mkdir()
        for name in ("a.c", "b.c", "api.h"):
            (self.pristine / name).write_text("original " + name)
        self.rules = {"source_roots": ["."], "allowed_files": ["a.c", "b.c"],
                      "allowed_extensions": [".c", ".h"], "protected_paths": ["api.h"],
                      "operations": ["modify"], "max_source_files": 2, "max_source_bytes": 1000}
        self.artifacts = {}
        for name in self.rules["allowed_files"]:
            path = self.root / ("export-" + name)
            path.write_text("changed " + name)
            self.artifacts[name] = path

    def admit(self, artifacts=None):
        return policy.artifacts_to_envelope(
            self.artifacts if artifacts is None else artifacts, self.pristine,
            self.root / "replayed", self.rules)

    def test_exact_regular_file_artifacts_preserve_protected_header(self):
        result = self.admit()
        self.assertEqual(result["files"], {"a.c": "changed a.c", "b.c": "changed b.c"})
        self.assertEqual((self.root / "replayed/api.h").read_text(), "original api.h")

    def test_missing_extra_or_whole_root_exports_rejected(self):
        for artifacts in ({"a.c": self.artifacts["a.c"]},
                          {**self.artifacts, "api.h": self.pristine / "api.h"},
                          {".": self.pristine}):
            with self.assertRaises(ValueError):
                self.admit(artifacts)

    def test_symlinked_file_export_rejected(self):
        self.artifacts["a.c"].unlink()
        self.artifacts["a.c"].symlink_to(self.pristine / "a.c")
        with self.assertRaisesRegex(ValueError, "regular"):
            self.admit()

    def test_directory_cannot_replace_a_file_export(self):
        self.artifacts["a.c"].unlink()
        self.artifacts["a.c"].mkdir()
        with self.assertRaisesRegex(ValueError, "regular"):
            self.admit()

    def test_pristine_internal_test_symlinks_preserved_but_not_editable(self):
        tests = self.pristine / "tests"
        tests.mkdir()
        (tests / "fixture").write_text("test bytes")
        (tests / "link").symlink_to("fixture")
        self.admit()
        self.assertEqual((self.root / "replayed/tests/link").readlink(), Path("fixture"))
        (self.root / "replayed/tests/link").unlink()
        (self.root / "replayed/tests/link").symlink_to("../api.h")
        with self.assertRaisesRegex(ValueError, "Protected"):
            policy.envelope(self.root / "replayed", self.pristine, self.rules)

    def test_new_or_escaping_protected_symlinks_are_rejected(self):
        outside = self.root / "outside"
        outside.write_text("not source")
        (self.pristine / "external").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.admit()

    def test_existing_upstream_openssl_directive_is_not_a_new_dependency(self):
        original = {"a.c": "#if CONFIG_OPENSSL\n#include <openssl/rand.h>\n#endif\nint x = 1;\n"}
        changed = {"a.c": original["a.c"].replace("x = 1", "x = 2")}
        rules = {**self.rules, "operations": ["modify", "add", "remove"]}
        policy.source_policy.validate(changed, rules, original=original)
        with self.assertRaisesRegex(ValueError, "dependency"):
            policy.source_policy.validate(
                {"a.c": changed["a.c"] + "#include <openssl/sha.h>\n"}, rules, original=original)


if __name__ == "__main__":
    unittest.main()
