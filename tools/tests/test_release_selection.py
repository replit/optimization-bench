"""The working selection is exact; membership never implies qualification."""
import json
from pathlib import Path
import unittest

from tools.release_preflight import inspect_release

ROOT = Path(__file__).resolve().parents[2]
CORE_TASKS = {
    "numpy-quantile",
    "sqlite-distinct-plant-facets",
    "sqlglot-mozilla-transpile",
    "git-history-change-summary",
}
# zrip-decode is a core task of the full staging checkout but is not exported.
if (ROOT / "tasks/zrip-decode").is_dir():
    CORE_TASKS.add("zrip-decode")
TASKS = {path.name for path in (ROOT / "tasks").iterdir()
         if (path / "task.json").is_file()}


class ReleaseSelectionTests(unittest.TestCase):
    def test_working_selection_matches_every_packaged_task(self):
        self.assertTrue(CORE_TASKS <= TASKS)
        self.assertFalse(TASKS & {"lame-mono-vbr-encode", "lumen-js",
                                 "universal-ctags-c-symbol-tags"})
        for name in ("all", "ready", "v1.0"):
            selection = json.loads((ROOT / f"datasets/{name}.json").read_text())
            self.assertEqual(set(selection["tasks"]), TASKS, name)
            self.assertEqual(len(selection["tasks"]), len(TASKS), name)

    def test_current_qualification_remains_pending_and_empty(self):
        selection = json.loads((ROOT / "datasets/v1.0.json").read_text())
        timing = json.loads((ROOT / selection["timing_qualification"]).read_text())
        self.assertEqual(selection["status"], "qualification-pending")
        self.assertEqual(timing["status"], "pending")
        self.assertEqual(timing["tasks"], {})

    def test_selected_tasks_export_and_register_without_claiming_native_execution(self):
        result = inspect_release(require_generic_hooks=True)
        self.assertTrue(result["passed"], result)
        self.assertEqual({row["task"] for row in result["tasks"]}, TASKS)
        for row in result["tasks"]:
            self.assertEqual(row["judge_smoke_test"], "not_run")
            self.assertEqual(row["timing_qualification"], "not_checked")
            self.assertEqual(row["generic_registration"]["native_execution"], "not_run")

    def test_agent_contexts_have_no_added_tool_bundle(self):
        for name in sorted(TASKS):
            task = ROOT / "tasks" / name
            for relative in ("environment/agent.Dockerfile",
                             "environment/docker-compose.yaml",
                             "environment/agent-tools"):
                self.assertFalse((task / relative).exists(), (name, relative))
            text = (task / "instruction.md").read_text()
            self.assertNotIn("Common measurement and debugging tools are installed", text)
            self.assertNotIn("py-spy", text)


if __name__ == "__main__":
    unittest.main()
