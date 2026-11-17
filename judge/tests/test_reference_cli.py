import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import public_input_preflight, reference, score, verifier


class ReferenceCliTests(unittest.TestCase):
    def test_direct_verifier_reuses_descriptor_relative_source_root_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, exports = root / "task", root / "other-directory/exports"
            task.mkdir()
            names = ("source/babel", "source/legacy")
            (task / "release.json").write_text(json.dumps({"source_policy": {"source_roots": list(names)}}))
            for name in names:
                (exports / name).mkdir(parents=True)
            descriptor = exports / "submission.json"
            descriptor.write_text(json.dumps({name: name for name in names}))
            output = root / "result.json"
            command = ["verifier", "--task", str(task), "--candidate", str(root / "candidate"),
                       "--projects", str(root / "projects"), "--image", "image", "--cpuset", "0-7",
                       "--cpu-class", "test", "--pristine", str(root / "pristine"),
                       "--artifacts", str(descriptor), "--output", str(output)]
            with patch("sys.argv", command), \
                    patch.object(verifier.policy, "artifacts_to_envelope", return_value={"files": {}}) as admit, \
                    patch.object(verifier, "image_id", return_value="image"), \
                    patch.object(score, "assets", return_value=root / "trusted"), \
                    patch.object(public_input_preflight, "ensure_public_inputs"), \
                    patch.object(reference, "ensure", return_value={"build": root / "reference"}), \
                    patch.object(verifier, "evaluate", return_value={"correct": True, "status": "passed"}):
                self.assertEqual(verifier.main(), 0)
            self.assertEqual(admit.call_args.args[0], {name: str(exports / name) for name in names})

    def test_printed_and_saved_descriptor_exposes_only_observed_execution_facts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, output = root / "task", root / "reference.json"
            fixture = {"root": root / "cache/reference", "build": root / "cache/reference/build",
                       "key": "fixture-reference", "build_sha256": "fixture-build"}
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(verifier, "image_id", return_value="fixture-image"), \
                    patch.object(score, "assets", return_value=root / "assets"), \
                    patch.object(public_input_preflight, "ensure_public_inputs") as inputs, \
                    patch.object(reference, "ensure", return_value=fixture), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(reference.main(["--task", str(task), "--image", "fixture",
                                                 "--cache", str(root / "cache"),
                                                 "--output", str(output)]), 0)
            inputs.assert_called_once_with(task, source=root / "assets/public-workload")
            value = json.loads(stdout.getvalue())
            self.assertEqual(value, json.loads(output.read_text()))
            self.assertFalse(value["execution"]["timing_performed"])
            self.assertFalse(value["claimable"])
            self.assertIsNone(value["execution"]["verified_cpuset"])
            self.assertEqual(value["execution"]["runtime_image_id"], "fixture-image")
            self.assertIn("untimed", stderr.getvalue())
