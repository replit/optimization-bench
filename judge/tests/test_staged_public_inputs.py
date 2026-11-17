import json
from pathlib import Path
import tempfile
import unittest
from judge.verifier import HarnessError, public_input_path


class StagedPublicInputs(unittest.TestCase):
    def test_frozen_manifest_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "task"
            original = task / "environment/workload/inputs"
            staged = root / "staged/inputs"
            original.mkdir(parents=True)
            staged.mkdir(parents=True)
            (original / "manifest.json").write_text('{"splits":{}}')
            (staged / "manifest.json").write_text('{"splits":{"invented":[]}}')
            with self.assertRaisesRegex(HarnessError, "frozen manifest"):
                public_input_path(task, staged)
            (staged / "manifest.json").write_bytes((original / "manifest.json").read_bytes())
            self.assertEqual(public_input_path(task, staged), staged.resolve())

    def test_locked_reference_metadata_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "task"
            original = task / "environment/workload/inputs"
            staged = root / "staged/inputs"
            original.mkdir(parents=True)
            staged.mkdir(parents=True)
            descriptor = {"format": "optimbench-locked-cases-v1",
                          "cases": "cases.json", "lock": "lock.json", "references": "outputs.json"}
            for path in (original, staged):
                (path / "manifest.json").write_text(json.dumps(descriptor))
                for name in ("cases.json", "lock.json", "outputs.json"):
                    (path.parent / name).write_text("{}")
            self.assertEqual(public_input_path(task, staged), staged.resolve())
            (staged.parent / "outputs.json").write_text('{"invented":"expected hash"}')
            with self.assertRaisesRegex(HarnessError, "reference metadata"):
                public_input_path(task, staged)
