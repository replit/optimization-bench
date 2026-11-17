import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from judge.process_state import dynamic_representative
from runner import Step


class DynamicRepresentativeTests(unittest.TestCase):
    def test_same_workload_arguments_and_dynamic_only_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "codec").touch()
            ctx = SimpleNamespace(build=root)
            step = Step([str(root / "codec"), "encode", "input", "output"], resident=False)
            recipe = {"representative_dynamic": {
                "replace_executable": "codec",
                "compile": ["cc", "-O3", "/app/build/source/codec.c", "-o", "/scratch/state-target"]}}
            prep, work = dynamic_representative([step], ctx, recipe)
            self.assertEqual(prep[0].argv, recipe["representative_dynamic"]["compile"])
            self.assertEqual(work[0].argv, ["/scratch/state-target", "encode", "input", "output"])
            self.assertFalse(work[0].resident)
            self.assertEqual(dynamic_representative([step], ctx, {}), ([], [step]))

    def test_reject_invalid_unmatched_or_static_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "codec").touch()
            ctx = SimpleNamespace(build=root)
            for changes in ({"replace_executable": "../codec"}, {"replace_executable": "/codec"},
                            {"compile": ["cc", "-static", "-o", "/scratch/state-target"]},
                            {"compile": ["cc"]}, {"replace_executable": "missing"}):
                recipe = {"replace_executable": "codec",
                          "compile": ["cc", "-o", "/scratch/state-target"], **changes}
                with self.assertRaises(ValueError):
                    dynamic_representative([Step([str(root / "codec")])], ctx,
                                           {"representative_dynamic": recipe})
