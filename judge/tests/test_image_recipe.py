import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import image_recipe, score


class PortableRecipeTests(unittest.TestCase):
    def test_private_build_suppresses_attestation_and_retains_protected_recipe_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "example"
            recipe = {"recipe_sha256": "a" * 64}
            missing = subprocess.CalledProcessError(1, ["docker", "image", "inspect"])
            with patch.object(image_recipe, "descriptor", return_value=recipe), \
                    patch.object(score, "image_id", side_effect=[missing, "fixture-image"]), \
                    patch.object(score.subprocess, "run") as build:
                self.assertEqual(score.ensure_image(task, None, root), "fixture-image")
            command = build.call_args.args[0]
            self.assertIn("--provenance=false", command)
            self.assertIn(image_recipe.LABEL + "=" + recipe["recipe_sha256"], command)

    def fixture(self, root):
        task = root / "tasks/example"
        (task / "environment").mkdir(parents=True)
        (task / "environment/Dockerfile").write_text("FROM pinned-base\n")
        (task / "environment/driver.py").write_text("print('protected')\n")
        (task / "payload.json").write_text(json.dumps({"files": []}))
        (task / "release.json").write_text(json.dumps({"baseline_source_sha256": "fixture-source"}))
        names = b"tasks/example/environment/Dockerfile\0tasks/example/environment/driver.py\0"
        return task, names

    def test_same_recipe_identity_different_runtime_image_ids_are_not_conflated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, names = self.fixture(root)
            with patch.object(image_recipe, "ROOT", root), \
                    patch.object(image_recipe.subprocess, "run", return_value=SimpleNamespace(stdout=names)):
                expected = image_recipe.descriptor(task)
            def command(args, **kwargs):
                return (SimpleNamespace(stdout=names) if args[0] == "git" else
                        SimpleNamespace(stdout=json.dumps({image_recipe.LABEL: expected["recipe_sha256"]})))
            with patch.object(image_recipe, "ROOT", root), patch.object(image_recipe.subprocess, "run", side_effect=command):
                first = image_recipe.observation(task, "fixture-image-A")
                second = image_recipe.observation(task, "fixture-image-B")
            self.assertEqual(first["recipe_sha256"], second["recipe_sha256"])
            self.assertNotEqual(first["runtime_image_id"], second["runtime_image_id"])
            self.assertTrue(first["recipe_label_matches"])
            self.assertFalse(first["binary_reproducibility_claimed"])

    def test_protected_recipe_change_changes_portable_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, names = self.fixture(root)
            with patch.object(image_recipe, "ROOT", root), \
                    patch.object(image_recipe.subprocess, "run", return_value=SimpleNamespace(stdout=names)):
                before = image_recipe.descriptor(task)
                (task / "environment/Dockerfile").write_text("FROM different-pinned-base\n")
                after = image_recipe.descriptor(task)
            self.assertNotEqual(before["recipe_sha256"], after["recipe_sha256"])

    def test_derived_bodies_use_original_packet_commitments_not_incidental_local_bytes(self):
        from judge import public_packet
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, names = self.fixture(root)
            body = task / "environment/workload/inputs/data/original.jsonl"
            body.parent.mkdir(parents=True)
            body.write_bytes(b"original")
            declaration = task / "environment" / public_packet.NAME
            packet = {"schema": public_packet.SCHEMA, "mounts": ["inputs/data"],
                      "derived_inputs": [{"destination": body.relative_to(task).as_posix(),
                                          "bytes": 8, "sha256": "a" * 64}]}
            declaration.write_text(json.dumps(packet))
            names += (str(body.relative_to(root)) + "\0" +
                      str(declaration.relative_to(root)) + "\0").encode()
            with patch.object(image_recipe, "ROOT", root), \
                    patch.object(image_recipe.subprocess, "run", return_value=SimpleNamespace(stdout=names)):
                before = image_recipe.descriptor(task)
                body.write_bytes(b"not-admitted-during-recipe-description")
                self.assertEqual(before, image_recipe.descriptor(task))
                self.assertNotIn("workload/inputs/data/original.jsonl", before["context"])
                self.assertIn(public_packet.NAME, before["context"])
                packet["derived_inputs"][0]["sha256"] = "b" * 64
                declaration.write_text(json.dumps(packet))
                self.assertNotEqual(before["recipe_sha256"], image_recipe.descriptor(task)["recipe_sha256"])
