import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from judge import policy, source_policy


class ProtectedLinks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pristine = self.root / "pristine"
        (self.pristine / "lib").mkdir(parents=True)
        (self.pristine / "docs").mkdir()
        (self.pristine / "lib/a.py").write_text("value=1\n")
        (self.pristine / "docs/news.txt").write_text("protected\n")
        (self.pristine / "NEWS.txt").symlink_to("docs/news.txt")
        self.rules = {"source_roots": ["lib"], "allowed_extensions": [".py"],
                      "max_source_files": 10, "max_source_bytes": 10000,
                      "protected_paths": [],
                      "protected_symlinks": {"NEWS.txt": "docs/news.txt"}}
        self.candidate = self.root / "candidate"
        shutil.copytree(self.pristine, self.candidate, symlinks=True)

    def test_exact_opt_in_protected_link_and_artifact_replay(self):
        expected = policy.envelope(self.candidate, self.pristine, self.rules)
        replay = self.root / "replay"
        observed = policy.artifacts_to_envelope(
            {"lib": self.candidate / "lib"}, self.pristine, replay, self.rules)
        self.assertEqual(observed, expected)
        self.assertTrue((replay / "NEWS.txt").is_symlink())

    def test_pristine_aliases_work_without_optional_declarations(self):
        rules = copy.deepcopy(self.rules)
        del rules["protected_symlinks"]
        policy.envelope(self.candidate, self.pristine, rules)
        replay = self.root / "undeclared-replay"
        policy.artifacts_to_envelope({"lib": self.candidate / "lib"}, self.pristine, replay, rules)
        self.assertEqual((replay / "NEWS.txt").readlink(), Path("docs/news.txt"))
        (self.candidate / "extra.txt").symlink_to("docs/news.txt")
        with self.assertRaises(ValueError):
            policy.envelope(self.candidate, self.pristine, rules)

    def test_changed_missing_or_escaping_links_are_rejected(self):
        link = self.candidate / "NEWS.txt"
        link.unlink()
        for target in ("lib/a.py", "/etc/passwd", "../outside", "./docs/news.txt"):
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                policy.envelope(self.candidate, self.pristine, self.rules)
            link.unlink()
        with self.assertRaises(ValueError):
            policy.envelope(self.candidate, self.pristine, self.rules)

    def test_pristine_only_mode_also_rejects_retarget_and_removal(self):
        rules = copy.deepcopy(self.rules)
        del rules["protected_symlinks"]
        link = self.candidate / "NEWS.txt"
        link.unlink()
        link.symlink_to("lib/a.py")
        with self.assertRaises(ValueError):
            policy.envelope(self.candidate, self.pristine, rules)
        link.unlink()
        with self.assertRaises(ValueError):
            policy.envelope(self.candidate, self.pristine, rules)

    def test_editable_link_is_never_permitted(self):
        path = self.candidate / "lib/a.py"
        path.unlink()
        path.symlink_to("../docs/news.txt")
        rules = copy.deepcopy(self.rules)
        rules["protected_symlinks"]["lib/a.py"] = "../docs/news.txt"
        with self.assertRaises(ValueError):
            policy.envelope(self.candidate, self.pristine, rules)

    def test_preserved_multisegment_extension(self):
        rules = {"source_roots": ["optlib"], "allowed_extensions": [".ctags", ".ctags.in"],
                 "protected_paths": []}
        self.assertTrue(source_policy.allowed("optlib/parser.ctags.in", rules))
        self.assertTrue(source_policy.allowed("optlib/parser.ctags", rules))
        self.assertFalse(source_policy.allowed("optlib/parser.in", rules))
        self.assertFalse(source_policy.allowed("optlib/.ctags", rules))
