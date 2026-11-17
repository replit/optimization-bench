import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from tools.fuzz_runners.collect_receipts import collect
from tools.fuzz_runners.receipt import sha256


class CalibrationPackagingTests(unittest.TestCase):
    def fixture(self, root):
        smoke = root / "smoke"
        (smoke / "scratch/probe/work").mkdir(parents=True)
        image = root / "image.json"
        image.write_text(json.dumps({"runner_image": "sha256:" + "a" * 64}))
        (smoke / "capabilities.json").write_text(json.dumps({
            "language": "python", "runner_image": "sha256:" + "a" * 64,
            "runtime_input_sha256": {"probe.py": "b" * 64}}))
        (smoke / "scratch/probe/step-0.stderr").write_text("raw evidence")
        (smoke / "scratch/probe/work/source.py").write_text("print('fixture')")
        return smoke, image

    def test_complete_source_and_raw_hash_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            smoke, image = self.fixture(root)
            result = collect(smoke, [image], root / "out")
            self.assertFalse(result["task_qualification"])
            self.assertEqual(result["raw_evidence"]["sha256"], sha256(root / "out/raw-evidence.tar.gz"))
            with tarfile.open(root / "out/raw-evidence.tar.gz") as archive:
                self.assertEqual(set(archive.getnames()), {"step-0.stderr", "work/source.py"})

    def test_missing_runtime_binding_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            smoke, image = self.fixture(root)
            (smoke / "capabilities.json").write_text('{"language":"python"}')
            with self.assertRaises(ValueError):
                collect(smoke, [image], root / "out")

    def test_wrong_image_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            smoke, image = self.fixture(root)
            image.write_text(json.dumps({"runner_image": "sha256:" + "c" * 64}))
            with self.assertRaises(ValueError):
                collect(smoke, [image], root / "out")


if __name__ == "__main__":
    unittest.main()
