"""Prompt parity only; no runtime lifecycle or scoring-policy changes."""
from pathlib import Path
import shutil
import json
import ast
import tempfile
import unittest

from tools.update_catalog import (
    OS_JOIN_POLICY, PROCESS_GLOBAL_STATE_POLICY, agent_instructions,
    neutral_prompt_tasks, validate_neutral_prompt,
    prompt_editable_files, prose_word_limit,
)

ROOT = Path(__file__).resolve().parents[2]


class OSJoinInstructionTests(unittest.TestCase):
    def test_openmp_eight_matches_development_and_measured_environments(self):
        expected = {
            "numpy-quantile", "sqlite-distinct-plant-facets",
        }
        for name in expected:
            task = ROOT / "tasks" / name
            with self.subTest(task=name):
                dockerfile = (task / "environment/Dockerfile").read_text()
                self.assertRegex(dockerfile, r"(?m)^ENV .*?\bOMP_NUM_THREADS=8(?:\s|$)")
                tree = ast.parse((task / "environment/workload/task.py").read_text())
                environments = [
                    ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "ENV"
                            for target in node.targets)
                ]
                self.assertEqual(len(environments), 1)
                self.assertEqual(environments[0]["OMP_NUM_THREADS"], "8")
                if name == "numpy-quantile":
                    self.assertEqual(environments[0]["OPENBLAS_NUM_THREADS"], "8")
                    self.assertIn("OPENBLAS_NUM_THREADS=8", dockerfile)

    def test_selected_prompts_are_neutral_and_generation_is_idempotent(self):
        names = neutral_prompt_tasks()
        selected = json.loads((ROOT / "datasets/v1.0.json").read_text())
        self.assertEqual(names, {
            name for name in selected["tasks"]
            if selected.get("task_versions", {}).get(
                name, selected.get("task_version", "1.0.0")) == "1.0.0"})
        self.assertTrue(names)
        for name in names:
            task = ROOT / "tasks" / name
            with self.subTest(task=name):
                validate_neutral_prompt(task)
                original = (task / "instruction.md").read_text()
                self.assertNotIn("OMP_NUM_THREADS", original)
                self.assertEqual(agent_instructions(task), original)
                setup = original.split("## Setup\n", 1)[1].split("\n## ", 1)[0]
                self.assertNotIn("Common measurement and debugging tools are installed", setup)
                self.assertNotIn("py-spy", setup)
                self.assertNotIn(OS_JOIN_POLICY, original)
                self.assertIn(
                    "When a public call returns (or the program exits, for command-line "
                    "workloads), nothing it started may still be running.", original)

    def test_generation_repairs_shared_rules_without_overwriting_task_facts(self):
        name = "sqlglot-mozilla-transpile"
        original = (ROOT / "tasks" / name / "instruction.md").read_text()
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / name
            task.mkdir()
            changed = original.replace("compatible additions are allowed.", "changed.")
            (task / "instruction.md").write_text(changed)
            self.assertEqual(agent_instructions(task), original)
            (task / "instruction.md").write_text(original)
            self.assertEqual(agent_instructions(task), original)

    def test_validation_rejects_hints_and_incorrect_inventory(self):
        name = "sqlite-distinct-plant-facets"
        source = ROOT / "tasks" / name
        original = (source / "instruction.md").read_text()
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / name
            task.mkdir()
            for filename in ("task.toml", "release.json"):
                shutil.copyfile(source / filename, task / filename)
            (task / "environment").mkdir()
            shutil.copyfile(source / "environment/pristine-files.lock.json",
                            task / "environment/pristine-files.lock.json")
            # The prose budget scales with the task's declared performance units.
            (task / "tests").mkdir()
            shutil.copyfile(source / "tests/judge.json", task / "tests/judge.json")
            for text, error in (
                (original.replace("## Workload\n", "## Workload\nUse threads.\n"),
                 "prohibited prompt wording"),
                (original.replace("under `src` and `ext`", "under `src`"),
                 "editable rule differs"),
                (original + "\n## Editable files\n", "editable appendices"),
            ):
                with self.subTest(error=error):
                    (task / "instruction.md").write_text(text)
                    with self.assertRaisesRegex(ValueError, error):
                        validate_neutral_prompt(task)
            (task / "instruction.md").write_text(original)
            validate_neutral_prompt(task)

    def test_closed_edit_rules_equal_allowed_files(self):
        expected_counts = {
            "sqlite-distinct-plant-facets": 229, "git-history-change-summary": 15,
        }
        for name, count in expected_counts.items():
            task = ROOT / "tasks" / name
            with self.subTest(task=name):
                text = (task / "instruction.md").read_text()
                expanded = prompt_editable_files(task, text)
                policy = json.loads((task / "release.json").read_text())["source_policy"]
                self.assertEqual(expanded, set(policy["allowed_files"]))
                self.assertEqual(len(expanded), count)
                lock = json.loads((task / "environment/pristine-files.lock.json").read_text())
                pristine = set(lock.get("files_sha256", lock))
                if name != "git-history-change-summary":
                    self.assertLessEqual(expanded, pristine)
                else:
                    # This lock protects noneditable source only. Require the
                    # literal rule rather than asserting an unproved wildcard.
                    self.assertIn("files are editable: ", text)
                    self.assertFalse(any(p.startswith("xdiff/") for p in pristine))


class ProseBudgetTests(unittest.TestCase):
    def test_budget_scales_with_declared_public_units_up_to_ceiling(self):
        cases = [(None, 800), ({}, 800), ({"performance_units": {"public": {"a": ["a"]}}}, 800),
                 ({"performance_units": {"public": {f"u{i}": [f"c{i}"] for i in range(3)},
                                         "heldout": {"h": ["h"]}}}, 880),
                 ({"performance_units": {"public": {f"u{i}": [f"c{i}"] for i in range(30)}}}, 1400)]
        for settings, limit in cases:
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as root:
                task = Path(root)
                (task / "tests").mkdir()
                if settings is not None:
                    (task / "tests/judge.json").write_text(json.dumps(settings))
                self.assertEqual(prose_word_limit(task), limit)
