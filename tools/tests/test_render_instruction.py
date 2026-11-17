import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import render_instruction as ri

ROOT = Path(__file__).resolve().parents[2]


def release_tasks():
    return json.loads((ROOT / "datasets/v1.0.json").read_text())["tasks"]


class RenderInstructionTests(unittest.TestCase):
    def test_every_template_renders_the_committed_instruction_with_defaults(self):
        tasks = sorted(path.parent for path in (ROOT / "tasks").glob(f"*/{ri.TEMPLATE}"))
        self.assertTrue(set(release_tasks()) <= {task.name for task in tasks})
        for task in tasks:
            with self.subTest(task=task.name):
                template = (task / ri.TEMPLATE).read_text()
                instruction = (task / "instruction.md").read_text()
                self.assertEqual(ri.render(template, ri.defaults(task)), instruction)
                self.assertEqual(ri.templatize(instruction, ri.defaults(task)), template)

    def test_release_templates_carry_the_time_limit_as_a_variable(self):
        for name in release_tasks():
            with self.subTest(task=name):
                task = ROOT / "tasks" / name
                template = (task / ri.TEMPLATE).read_text()
                self.assertIn("{{ agent_timeout_seconds }}", template)
                rendered = ri.render(template, {**ri.defaults(task), "agent_timeout": "10m"})
                self.assertIn("600", rendered)
                self.assertIn("10 minutes", rendered)
                self.assertNotIn("7,200", rendered)
                self.assertNotIn("2 hours", rendered)

    def test_durations(self):
        self.assertEqual(ri.parse_duration("600"), 600)
        self.assertEqual(ri.parse_duration("90m"), 5400)
        self.assertEqual(ri.parse_duration("2h"), 7200)
        self.assertEqual(ri.human_duration(7200), "2 hours")
        self.assertEqual(ri.human_duration(3600), "1 hour")
        self.assertEqual(ri.human_duration(5400), "90 minutes")
        self.assertEqual(ri.human_duration(45), "45 seconds")
        for bad in ("0", "-5m", "2d", "soon"):
            with self.assertRaises(ValueError):
                ri.parse_duration(bad)

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaises(ValueError):
            ri.render("{{ machine_type }}", {"agent_timeout": 7200})

    def test_cli_rewrites_local_instruction_and_restores_defaults(self):
        name = "sqlglot-mozilla-transpile"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "tasks" / name
            task.mkdir(parents=True)
            for item in ("task.toml", "instruction.md", ri.TEMPLATE):
                shutil.copy(ROOT / "tasks" / name / item, task / item)
            original = (task / "instruction.md").read_text()
            with mock.patch.object(ri, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                ri.main(["--all", "--set", "agent_timeout=30m"])
                self.assertIn("1,800-second agent timeout (30 minutes)", (task / "instruction.md").read_text())
                ri.main(["--task", name])
            self.assertEqual((task / "instruction.md").read_text(), original)


if __name__ == "__main__":
    unittest.main()
