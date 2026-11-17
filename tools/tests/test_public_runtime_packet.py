import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from judge import public_packet
from tools import public_runtime_packet


class AgentPacketTests(unittest.TestCase):
    def test_wrapper_preserves_original_task_policy_and_only_mounts_declared_public_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "task"
            environment = task / "environment"
            environment.mkdir(parents=True)
            config = ('schema_version="1.4"\nartifacts=[{source="/app/source/src"}]\n'
                      '[agent]\nnetwork_mode="no-network"\n[environment]\n'
                      '# docker_image="private-placeholder"\ncpus=8\nmemory_mb=16384\n'
                      'network_mode="no-network"\n[verifier]\nenvironment_mode="separate"\n')
            (task / "task.toml").write_text(config)
            declaration = {"schema": public_packet.SCHEMA, "mounts": ["inputs/files"]}
            (environment / public_packet.NAME).write_text(json.dumps(declaration))
            trusted = root / "trusted"
            files = trusted / "public-workload/inputs/files"
            files.mkdir(parents=True)
            (files / "original").write_text("original bytes")
            (task / "instruction.md").write_text("original prompt")
            for name in ("tests", "solution"):
                (task / name).mkdir()
                (task / name / "private").write_text("private verifier body")
            (task / "private-root-receipt.json").write_text("private root receipt")
            with patch.object(public_runtime_packet.score, "ensure_image", return_value="sha256:" + "a" * 64), \
                    patch.object(public_runtime_packet.score, "assets", return_value=trusted), \
                    patch.object(public_runtime_packet.public_input_preflight, "ensure_public_inputs",
                                 return_value={"scope": "public"}) as preflight:
                packet = public_runtime_packet.prepare(task, "image", root / "cache", root / "packet")
            self.assertEqual(preflight.call_count, 2)
            self.assertEqual(packet["mounts"][0]["target"], "/app/workload/inputs/files")
            self.assertTrue(packet["mounts"][0]["read_only"])
            self.assertFalse(packet["mounts"][0]["bind"]["create_host_path"])
            wrapper = Path(packet["harbor_task"])
            updated = tomllib.loads((wrapper / "task.toml").read_text())
            original = tomllib.loads(config)
            image = updated["environment"].pop("docker_image")
            self.assertEqual(updated, original)
            self.assertTrue(image.startswith("sha256:"))
            self.assertEqual((wrapper / "instruction.md").read_text(), "original prompt")
            self.assertEqual({path.name for path in wrapper.iterdir()},
                             {"instruction.md", "task.toml", "environment", "tests"})
            self.assertFalse(any(path.is_symlink() for path in wrapper.rglob("*")))
            for path in wrapper.rglob("*"):
                self.assertTrue(path.resolve().is_relative_to(wrapper))
                if path.is_file():
                    self.assertNotIn("private verifier body", path.read_text())
                    self.assertNotIn("private root receipt", path.read_text())
            rejected = subprocess.run(["sh", str(wrapper / "tests/test.sh")],
                                      capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("canonical judge.score separately", rejected.stderr)
            self.assertEqual(rejected.stdout, "")
            self.assertFalse(list(wrapper.rglob("reward.*")))
            command = (root / "packet/agent-command.txt").read_text()
            self.assertIn("--disable-verification", command)
            self.assertNotIn("--privileged", command)
            self.assertNotIn("SYS_PTRACE", command)
            self.assertNotIn("calibration", command)

    def test_ordinary_bundled_images_require_no_new_declaration_or_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "ordinary bundled images are unchanged"):
                public_runtime_packet.prepare(root / "task", "image", root / "cache", root / "packet")
            self.assertFalse((root / "packet").exists())


if __name__ == "__main__":
    unittest.main()
