"""Process-state probes of input-free images see the same read-only public inputs as an agent."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import process_state

ROOT = Path(__file__).resolve().parents[2]


class Stop(Exception):
    pass


def captured_flags(task, public_inputs):
    seen = {}

    def executor(image, cpuset, extra_docker_flags=(), **kwargs):
        seen["flags"] = list(extra_docker_flags)
        raise Stop

    with tempfile.TemporaryDirectory() as build, \
            patch.object(process_state, "DockerExecutor", side_effect=executor), \
            patch.object(process_state, "representative", return_value=None):
        try:
            process_state.probe(ROOT / "tasks" / task, build, "image", "0", public_inputs)
        except Stop:
            pass
    return seen.get("flags", [])


class ProcessStateInputsTests(unittest.TestCase):
    def test_input_free_task_mounts_its_runtime_inputs_read_only(self):
        with tempfile.TemporaryDirectory() as inputs:
            flags = captured_flags("sqlglot-mozilla-transpile", inputs)
        self.assertIn(f"{Path(inputs).resolve()}:/app/workload/inputs:ro", flags)

    def test_image_contained_inputs_are_not_overlaid(self):
        with tempfile.TemporaryDirectory() as inputs:
            flags = captured_flags("black-project-format", inputs)
        self.assertTrue(flags, "probe did not reach the container launch")
        self.assertFalse(any(flag.endswith(":/app/workload/inputs:ro") for flag in flags))


if __name__ == "__main__":
    unittest.main()
