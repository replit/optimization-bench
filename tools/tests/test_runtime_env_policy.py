from pathlib import Path
import tempfile
import unittest

from tools.runtime_env_policy import audit, development_env

ROOT = Path(__file__).resolve().parents[2]


class RuntimeEnvParityTests(unittest.TestCase):
    def test_all_task_development_recipes_match_frozen_worker_runtime_knobs(self):
        for task in sorted((ROOT / "tasks").iterdir()):
            if (task / "task.json").is_file():
                with self.subTest(task=task.name):
                    record = audit(task)
                    self.assertTrue(record["matched"], record)

    def test_multistage_env_does_not_leak_builder_only_knobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "environment").mkdir()
            (task / "environment/Dockerfile").write_text(
                "FROM base AS builder\nENV GOMAXPROCS=1\nFROM base AS final\nENV OMP_NUM_THREADS=8\n")
            self.assertEqual(development_env(task), {"OMP_NUM_THREADS": "8"})

    def test_sqlglot_final_image_preserves_the_measured_python_hash_seed(self):
        record = audit(ROOT / "tasks/sqlglot-mozilla-transpile")
        self.assertEqual(record["measured"]["PYTHONHASHSEED"], "0")
        self.assertEqual(record["development"]["PYTHONHASHSEED"], "0")
