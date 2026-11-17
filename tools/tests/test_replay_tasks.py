import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import replay_tasks as replay


class ReplayPublicInputsTests(unittest.TestCase):
    def fixture(self, root, bundled=None, recipe=True):
        task = root / "tasks/example"
        inputs = task / "environment/workload/inputs"
        inputs.mkdir(parents=True)
        (inputs / "manifest.json").write_text("{}")
        body = b"synthetic public source"
        entry = {"scope": "public", "destination": "environment/workload/inputs/files/body",
                 "name": "body", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        if recipe:
            entry["fetch"] = {"kind": "literal", "base64": base64.b64encode(body).decode()}
        private = {"scope": "heldout", "destination": "tests/heldout/private", "name": "private",
                   "bytes": 6, "sha256": hashlib.sha256(b"secret").hexdigest(),
                   "fetch": {"kind": "must-not-fetch"}}
        (task / "payload.json").write_text(json.dumps({"files": [entry, private]}))
        runner = root / "tools/runner/runner.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("synthetic runner identity\n")
        image_workload = root / "image-workload"
        (image_workload / "inputs").mkdir(parents=True)
        shutil.copyfile(inputs / "manifest.json", image_workload / "inputs/manifest.json")
        if bundled is not None:
            (image_workload / "inputs/files").mkdir()
            (image_workload / "inputs/files/body").write_bytes(bundled)
        return task, image_workload, body

    def replay_fixture(self, root, task, image_workload, body, *, skip_build, prepare=None):
        target = root / "output/example"
        runtime = target / "public-workload/inputs"
        events = []
        builds = []
        expected = {"path": "synthetic-output", "expect_len": 4,
                    "expect_sha": hashlib.sha256(b"test").hexdigest()}
        line = f'{expected["path"]}\t{expected["expect_len"]}\t{expected["expect_sha"]}\n'
        ensure = replay.public_input_preflight.ensure_public_inputs

        def preflight(selected, **kwargs):
            self.assertEqual(selected, task)
            events.append("prepare-runtime" if "source" in kwargs else "prepare-build")
            if "source" in kwargs:
                self.assertEqual(kwargs["source"], runtime.parent)
                self.assertIs(kwargs["fetch"], True)
            if prepare is not None:
                return prepare(selected, **kwargs)
            return ensure(selected, root=root, **kwargs)

        def check_output(command, **kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                return "sha256:" + "a" * 64
            self.assertEqual(command, ["docker", "create", "consolidation:example"])
            return "synthetic-container"

        def run(command, **kwargs):
            command = list(map(str, command))
            if command[:2] == ["docker", "build"]:
                events.append("build")
                builds.append(kwargs["env"])
                self.assertEqual((task / "environment/workload/inputs/files/body").read_bytes(), body)
            elif command[:2] == ["docker", "cp"]:
                source, destination = command[-2:]
                if source.endswith(":/app/baseline-build"):
                    Path(destination).mkdir()
                elif source.endswith(":/app/workload"):
                    shutil.copytree(image_workload, destination)
                else:
                    self.assertTrue(source.endswith(":/app/projects"))
                    return subprocess.CompletedProcess(command, 1)
            elif command[:2] == ["docker", "rm"]:
                pass
            elif command[:2] == ["docker", "run"]:
                events.append("baseline")
                mount = next(csv.reader([command[command.index("--mount") + 1]]))
                self.assertEqual(mount, ["type=bind", "source=" + str(runtime),
                                         "target=/app/workload/inputs", "readonly"])
                self.assertEqual(command[command.index("--network") + 1], "none")
                self.assertEqual(command[command.index("--cpuset-cpus") + 1], "0-7")
                self.assertEqual(command[command.index("--memory") + 1], "16g")
                self.assertEqual((runtime / "files/body").read_bytes(), body)
                kwargs["stdout"].write(line.encode())
            elif "--executor" in command:
                events.append("docker-executor")
                self.assertEqual(command[command.index("--inputs") + 1], str(runtime))
                self.assertEqual((runtime / "files/body").read_bytes(), body)
                kwargs["stdout"].write(line.encode())
            else:
                self.assertEqual(command[1:3], ["-m", "judge.verifier"])
                events.append("verifier")
                self.assertEqual(command[command.index("--public-inputs") + 1], str(runtime))
                self.assertEqual((runtime / "files/body").read_bytes(), body)
                Path(command[command.index("--output") + 1]).write_text('{"correct": true}')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(replay, "ROOT", root), patch.object(replay, "subprocess") as process, \
                patch.object(replay.public_input_preflight, "ensure_public_inputs", side_effect=preflight), \
                patch.object(replay, "load"), patch.object(replay, "references", return_value=[expected]), \
                patch.dict(os.environ, {"DOCKER_BUILDKIT": "0"}):
            process.run.side_effect = run
            process.check_output.side_effect = check_output
            receipt = replay.replay("example", "0-7", root / "output", skip_build)
        self.assertFalse((task / "tests/heldout/private").exists())
        self.assertFalse((runtime / "private").exists())
        return receipt, events, builds

    def test_input_free_image_prepared_before_both_parity_arms_including_skip_build(self):
        for skip_build in (False, True):
            with self.subTest(skip_build=skip_build), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task, workload, body = self.fixture(root)
                receipt, events, builds = self.replay_fixture(
                    root, task, workload, body, skip_build=skip_build)
                self.assertTrue(receipt["passed"], receipt)
                self.assertTrue(receipt["noop_accepted"])
                self.assertFalse(receipt["claimable"])
                self.assertEqual(receipt["public_input_preflight"]["runtime_cache_materialized_entries"], 1)
                execution = ["prepare-runtime", "baseline", "docker-executor", "verifier"]
                self.assertEqual(events, execution if skip_build else ["prepare-build", "build"] + execution)
                self.assertEqual([env["DOCKER_BUILDKIT"] for env in builds], [] if skip_build else ["1"])

    def test_skip_build_restores_existing_bundled_inputs_without_new_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, workload, body = self.fixture(root, bundled=b"synthetic public source", recipe=False)
            import payload_check
            with patch.object(payload_check.Fetcher, "render", side_effect=AssertionError("no acquisition")):
                receipt, events, builds = self.replay_fixture(
                    root, task, workload, body, skip_build=True)
            self.assertTrue(receipt["passed"], receipt)
            self.assertEqual(receipt["public_input_preflight"]["cache_restored_entries"], 1)
            self.assertEqual(events, ["prepare-runtime", "baseline", "docker-executor", "verifier"])
            self.assertEqual(builds, [])

    def test_failed_preparation_prevents_build_and_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, workload, body = self.fixture(root)
            def reject(*args, **kwargs):
                raise replay.public_input_preflight.PublicInputsError("synthetic input mismatch")
            receipt, events, builds = self.replay_fixture(
                root, task, workload, body, skip_build=False, prepare=reject)
            self.assertFalse(receipt["passed"])
            self.assertIn("synthetic input mismatch", receipt["error"])
            self.assertEqual(events, ["prepare-build"])
            self.assertEqual(builds, [])

    def test_corrupt_image_cache_fails_before_either_parity_arm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, workload, body = self.fixture(root, bundled=b"corrupt cached source")
            receipt, events, builds = self.replay_fixture(
                root, task, workload, body, skip_build=True)
            self.assertFalse(receipt["passed"])
            self.assertIn("Public cache restoration failed", receipt["error"])
            self.assertEqual(events, ["prepare-runtime"])
            self.assertEqual(builds, [])


if __name__ == "__main__":
    unittest.main()
