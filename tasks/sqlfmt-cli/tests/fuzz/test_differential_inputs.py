import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).parents[1] / "differential/generate.py"
SPEC = importlib.util.spec_from_file_location("sqlfmt_differential_inputs", PATH)
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class DifferentialInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "files").mkdir()
        raw = b"CLI1\nsqlfmt\nstdin\n-\nselect 1;\n"
        self.digest = hashlib.sha256(raw).hexdigest()
        (self.root / "files" / self.digest).write_bytes(raw)
        (self.root / "manifest.json").write_text(json.dumps({
            "splits": {"train": [{"docs": [{"sha256": self.digest, "size": len(raw)}]}]}
        }))

    def test_native_documents_do_not_enter_the_cli_framing_parser(self):
        raw = b"select 'native input without CLI framing';"
        (self.root / "files" / hashlib.sha256(raw).hexdigest()).write_bytes(raw)
        self.assertEqual(GENERATOR.public_sql(self.root), ["select 1;\n"])

    def test_unlisted_cli_bodies_do_not_expand_the_frozen_generator_pool(self):
        raw = b"CLI1\nsqlfmt\nstdin\n-\nselect 2;\n"
        (self.root / "files" / hashlib.sha256(raw).hexdigest()).write_bytes(raw)
        self.assertEqual(GENERATOR.public_sql(self.root), ["select 1;\n"])

    def test_corrupt_declared_cli_input_fails_closed(self):
        (self.root / "files" / self.digest).write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "checksum"):
            GENERATOR.public_sql(self.root)


if __name__ == "__main__":
    unittest.main()
