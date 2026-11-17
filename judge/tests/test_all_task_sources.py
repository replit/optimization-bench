"""Every common-shell task is addressable by score asset extraction."""
import json
from pathlib import Path
import unittest
from judge.gates import source_path

ROOT = Path(__file__).resolve().parents[2]


class SourcePaths(unittest.TestCase):
    def test_all_tasks_have_a_source_path(self):
        for name in json.loads((ROOT / "datasets/all.json").read_text())["tasks"]:
            with self.subTest(task=name):
                value = source_path(ROOT / "tasks" / name)
                self.assertTrue(value.startswith("/app/"))
                self.assertNotIn("..", Path(value).parts)
