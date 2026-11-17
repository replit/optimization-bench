"""Reference cache provenance, exact admissions and shared call-site selection."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import reference, score, verifier, calibration


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class ReferenceTests(unittest.TestCase):
    def test_public_gate_reference_never_acquires_private_and_cannot_admit_timing(self):
        # A public-only correctness replay may read package metadata, but must
        # not authenticate/acquire an unavailable private body.
        lock = json.loads((self.task / "tests/heldout.lock.json").read_text())
        lock["status"] = "available"
        write(self.task / "tests/heldout.lock.json", lock)
        missing = self.task / "tests/heldout/expected/bevy"
        missing.parent.mkdir(parents=True)
        missing.write_text("version https://git-lfs.github.com/spec/v1\n"
                           "oid sha256:" + "a" * 64 + "\nsize 1000\n")
        with patch("judge.heldout.materialized_task", side_effect=AssertionError("private acquisition")), \
             patch("judge.heldout.check", side_effect=AssertionError("private authentication")):
            entry = reference.ensure(self.task, self.trusted, "image", "0-7",
                                     self.root / "cache", public_only=True)
            self.assertTrue(entry["manifest"]["descriptor"]["public_only"])
            self.assertEqual(self.outputs.call_count, 1)
            self.assertFalse(self.outputs.call_args.kwargs["private"])
            reference.verify_for(self.task, self.trusted, "image", entry, public_only=True)
            with self.assertRaisesRegex(reference.QualificationError, "cannot admit a measurement"):
                reference.validate_call(self.task, "image", entry["build"], entry)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task, self.trusted = self.root / "fixture", self.root / "image-assets"
        for path in (self.task / "tests", self.task / "environment/workload",
                     self.trusted / "pristine", self.trusted / "baseline",
                     self.trusted / "projects"):
            path.mkdir(parents=True)
        self.rules = {"source_roots": ["."], "allowed_files": ["marker.py"],
                      "allowed_extensions": [".py"], "protected_paths": [],
                      "max_source_files": 5, "max_source_bytes": 10000}
        write(self.task / "release.json", {"source_policy": self.rules})
        write(self.task / "task.json", {})
        write(self.task / "tests/judge.json", {})
        write(self.task / "tests/heldout/manifest.json", {"splits": {}})
        write(self.task / "tests/heldout.lock.json",
              {"schema_version": 1, "status": "unavailable", "files": {
                  "heldout/manifest.json": hashlib.sha256(
                      (self.task / "tests/heldout/manifest.json").read_bytes()).hexdigest()},
               "archives": []})
        (self.trusted / "pristine/marker.py").write_text("value = 1\n")
        (self.trusted / "baseline/program").write_bytes(b"image baseline")
        self.suites = {"upstream": {"passed": True, "outcomes": {"original-case": "passed"}}}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("judge.verifier.image_id", side_effect=lambda x: x))
        self.stack.enter_context(patch("judge.interfaces.check", return_value={"passed": True}))
        self.real_outputs = reference.outputs
        self.outputs = self.stack.enter_context(patch.object(
            reference, "outputs", return_value={"passed": True, "cases": []}))
        self.stack.enter_context(patch("judge.verifier.public_input_path",
                                     return_value=self.root / "inputs"))
        self.gates = self.stack.enter_context(patch("judge.gates.execute_suites", return_value=self.suites))
        self.builder = self.stack.enter_context(patch("judge.build.build", side_effect=self.build))

    def build(self, task, pristine, image, cpuset, output, source=None, **kwargs):
        self.assertEqual(source, self.trusted / "pristine")
        self.assertNotIn("seed_build", kwargs)
        output.mkdir()
        shutil.copytree(pristine, output / "source")
        (output / "build").mkdir()
        (output / "build/program").write_bytes(b"independently rebuilt")
        admitted = reference.descriptor(task, self.trusted, image)
        receipt = {"task": task.name, "image": image, "source_sha256": admitted["source_sha256"],
                   "build_sha256": verifier.build_inventory(output / "build"),
                   "compiler_cache_seed": None}
        write(output / "build-receipt.json", receipt)
        return receipt

    def ensure(self, image="image"):
        return reference.ensure(self.task, self.trusted, image, "0-7", self.root / "cache")

    def test_cache_builds_once_and_keeps_legacy_image_assets_unchanged(self):
        original = verifier.build_inventory(self.trusted)
        first, second = self.ensure(), self.ensure()
        self.assertEqual(first["root"], second["root"])
        self.assertNotEqual(first["build"], self.trusted / "baseline")
        self.assertEqual(self.builder.call_count, 1)
        self.assertEqual(self.gates.call_count, 2)  # Image oracle and rebuilt reference
        self.assertEqual(verifier.build_inventory(self.trusted), original)
        self.assertFalse((self.trusted / "upstream-reference.json").exists())
        self.assertTrue(self.outputs.call_args_list[0].kwargs["private"] is False)

    def test_key_invalidates_image_source_configuration_and_helper_protocol(self):
        first = self.ensure()
        self.assertNotEqual(first["key"], self.ensure("another-image")["key"])
        (self.trusted / "pristine/marker.py").write_text("value = 2\n")
        source = self.ensure()
        self.assertNotEqual(first["key"], source["key"])
        write(self.task / "tests/judge.json", {"memory": "2g"})
        config = self.ensure()
        self.assertNotEqual(source["key"], config["key"])
        probe = self.task / "tests/differential/run.py"
        probe.parent.mkdir()
        probe.write_text("def probe(): return 1\n")
        differential = self.ensure()
        self.assertNotEqual(config["key"], differential["key"])
        old_sha = reference._sha
        with patch.object(reference, "_sha",
                          side_effect=lambda p: "changed" if Path(p) == Path(reference.__file__) else old_sha(p)):
            self.assertNotEqual(differential["key"], self.ensure()["key"])
        with patch.object(reference, "_sha",
                          side_effect=lambda p: "changed" if Path(p).name == "process_probe.c" else old_sha(p)):
            self.assertNotEqual(differential["key"], self.ensure()["key"])

    def test_tampered_build_source_receipts_and_identity_fail_closed(self):
        for target in ("built/build/program", "built/source/marker.py",
                       "built/build-receipt.json", "admission.json", "reference.json"):
            with self.subTest(target=target):
                cache = self.root / "cache"
                if cache.exists():
                    shutil.rmtree(cache)
                entry = self.ensure()
                path = entry["root"] / target
                path.write_bytes(b"tampered")
                with self.assertRaises((ValueError, KeyError)):
                    self.ensure()

    def test_exact_output_failure_never_publishes_or_regenerates_expected(self):
        value = (self.task / "tests/heldout.lock.json").read_bytes()
        self.outputs.side_effect = reference.QualificationError("frozen public outputs differ")
        with self.assertRaisesRegex(reference.QualificationError, "task qualification failed"):
            self.ensure()
        self.assertEqual((self.task / "tests/heldout.lock.json").read_bytes(), value)
        self.assertFalse(list((self.root / "cache").rglob("reference.json")))

    def test_executable_mode_tamper_is_rejected_on_read(self):
        entry = self.ensure()
        program = entry["build"] / "program"
        program.chmod(program.stat().st_mode ^ 0o111)
        with self.assertRaisesRegex(reference.QualificationError, "permissions changed"):
            self.ensure()

    def test_changed_upstream_identities_never_publish(self):
        self.gates.side_effect = [self.suites, {
            "upstream": {"passed": True, "outcomes": {"different-case": "passed"}}}]
        with self.assertRaises(reference.QualificationError):
            self.ensure()
        self.assertFalse(list((self.root / "cache").rglob("reference.json")))

    def test_failed_pristine_upstream_is_task_qualification_not_candidate_rejection(self):
        from judge.gates import UpstreamRejected
        self.gates.side_effect = UpstreamRejected("original suite failed", {"upstream": {}})
        with self.assertRaisesRegex(reference.QualificationError, "task qualification failed"):
            self.ensure()
        self.assertFalse(list((self.root / "cache").rglob("reference.json")))

    def test_available_private_contract_is_also_admitted(self):
        import hashlib
        digest = hashlib.sha256(b"private fixture").hexdigest()
        payload = self.task / "tests/heldout/files" / digest
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"private fixture")
        manifest = self.task / "tests/heldout/manifest.json"
        write(manifest, {"splits": {"private": [{"docs": [
            {"sha256": digest, "expect_sha": digest, "expect_len": 15}]}]}})
        write(self.task / "tests/heldout.lock.json",
              {"schema_version": 1, "status": "available", "files": {
                  "heldout/manifest.json": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  "heldout/files/" + digest: digest}, "archives": []})
        entry = self.ensure()
        self.assertEqual([call.kwargs["private"] for call in self.outputs.call_args_list], [False, True])
        admission = json.loads((entry["root"] / "admission.json").read_text())
        self.assertTrue(admission["heldout_outputs"]["passed"])

    def test_output_admission_uses_frozen_exact_bytes_not_numeric_tolerance(self):
        write(self.task / "tests/judge.json", {"memory": "1g", "pids_limit": 64})
        module = SimpleNamespace(cases=lambda _: [SimpleNamespace(id="case")],
                                 compare=lambda *args: True)
        import hashlib
        expected = [{"path": "case", "expect_len": 1,
                     "expect_sha": hashlib.sha256(b"A").hexdigest()}]
        with patch("judge.verifier.load_task", return_value=module), \
             patch("runner.references", return_value=expected), \
             patch("runner.run_case", return_value=({"case": b"B"}, [])), \
             patch("docker_executor.DockerExecutor"):
            with self.assertRaisesRegex(reference.QualificationError, "frozen public outputs"):
                self.real_outputs(self.task, self.trusted / "baseline", self.root / "inputs",
                                  self.root / "projects", "image", "0-7", private=False)

    def test_direct_api_rejects_image_baseline_or_unadmitted_reference(self):
        entry = self.ensure()
        with self.assertRaisesRegex(reference.QualificationError, "Baseline is not"):
            reference.validate_call(self.task, "image", self.trusted / "baseline", entry)
        with self.assertRaises(reference.QualificationError):
            reference.validate_call(self.task, "image", entry["build"], None)
        self.assertEqual(reference.validate_call(self.task, "image", entry["build"], entry)["key"],
                         entry["key"])

    def test_upstream_receipt_binds_rebuilt_reference_not_image_inventory(self):
        entry = self.ensure()
        candidate = self.root / "candidate"
        shutil.copytree(entry["root"] / "built", candidate)
        path, _ = score.upstream_gate(self.task, self.trusted, candidate, "image",
                                       {"cpuset": "0-7"}, self.root / "out", self.root / "cache", entry)
        value = json.loads(path.read_text())
        self.assertEqual(value["reference_key"], entry["key"])
        self.assertEqual(value["reference_build_sha256"], entry["build_sha256"])
        self.assertNotEqual(value["reference_build_sha256"], verifier.build_inventory(self.trusted / "baseline"))
        self.assertTrue(value["identical_candidate_gate_reused"])


class ReferenceBoundaryTests(unittest.TestCase):
    def test_verifier_cli_reports_qualification_failure_without_candidate_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "task"
            write(task / "release.json", {"source_policy": {}})
            output = root / "result.json"
            arguments = ["verifier", "--task", str(task), "--candidate", str(root / "candidate"),
                         "--projects", str(root / "projects"), "--image", "image",
                         "--cpuset", "0-7", "--cpu-class", "cpu", "--pristine", str(root / "pristine"),
                         "--source", str(root / "source"), "--output", str(output)]
            with patch.object(sys, "argv", arguments), \
                 patch.object(verifier.policy, "envelope", return_value={"files": {}}), \
                 patch.object(verifier, "image_id", return_value="image"), \
                 patch.object(score, "assets", return_value=root / "assets"), \
                 patch("judge.public_input_preflight.ensure_public_inputs") as inputs, \
                 patch.object(reference, "ensure", side_effect=reference.QualificationError("failed")), \
                 patch.object(verifier, "evaluate") as evaluate:
                self.assertEqual(verifier.main(), 2)
            inputs.assert_called_once_with(task, source=root / "assets/public-workload")
            value = json.loads(output.read_text())
            self.assertEqual(value["status"], "invalid run")
            self.assertEqual(value["task_qualification"], "failed")
            self.assertIsNone(value["correct"])
            evaluate.assert_not_called()

    def test_heldout_check_uses_reference_for_upstream_and_exact_comparison(self):
        from judge import heldout_check
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            task = root / "task"
            write(task / "release.json", {"source_policy": {}})
            ref = {"build": root / "reference/build", "key": "key", "build_sha256": "ref"}
            patches = {
                "heldout.check": {"status": "available"}, "telemetry.pinned": list(range(8)),
                "verifier.image_id": "image", "score.assets": root / "trusted",
                "public_input_preflight.ensure_public_inputs": {},
                "reference.ensure": ref, "build.build": {"build_sha256": "candidate"},
                "policy.envelope": {"files": {}}, "score.upstream_gate": (root / "receipt.json", "sha"),
                "upstream.validate_receipt": {}, "verifier.build_inventory": "candidate",
                "compare": {"correct": True, "status": "heldout check passed"},
                "heldout.sha": "lock-sha",
            }
            mocks = {name: stack.enter_context(patch("judge.heldout_check." + name, return_value=value))
                     for name, value in patches.items()}
            result = heldout_check.run(task, "image", "0-7", root / "work", root / "cache")
            self.assertEqual(mocks["compare"].call_args.args[1], ref["build"])
            self.assertEqual(mocks["score.upstream_gate"].call_args.args[-1], ref)
            self.assertEqual(result["reference_build_sha256"], "ref")

    def test_heldout_resolves_multi_root_artifacts_relative_to_descriptor_not_cwd(self):
        from judge import heldout_check
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            task = root / "task"
            names = ["source/babel", "source/legacy"]
            write(task / "release.json", {"source_policy": {"source_roots": names}})
            exports = root / "unrelated-exports"
            for name in names:
                (exports / name).mkdir(parents=True)
            self.assertNotEqual(exports, Path.cwd())
            patches = {
                "heldout.check": {}, "telemetry.pinned": list(range(8)),
                "verifier.image_id": "image", "score.assets": root / "trusted",
                "public_input_preflight.ensure_public_inputs": {},
                "reference.ensure": {"build": root / "reference", "key": "key", "build_sha256": "ref"},
                "build.build": {"build_sha256": "candidate"}, "policy.envelope": {"files": {}},
                "score.upstream_gate": (root / "receipt", "sha"), "upstream.validate_receipt": {},
                "verifier.build_inventory": "candidate",
                "compare": {"correct": True, "status": "heldout check passed"}, "heldout.sha": "lock",
            }
            mocks = {name: stack.enter_context(patch("judge.heldout_check." + name, return_value=value))
                     for name, value in patches.items()}
            absolute = {name: str(exports / name) for name in names}
            for index, mapping in enumerate(({name: name for name in names}, absolute,
                                              {"artifacts": {name: name for name in names}})):
                with self.subTest(mapping=mapping):
                    descriptor = exports / "submission.json"
                    write(descriptor, mapping)
                    result = heldout_check.run(task, "image", "0-7", root / ("work" + str(index)),
                                               root / "cache", artifacts=descriptor)
                    self.assertTrue(result["correct"])
                    self.assertEqual(mocks["build.build"].call_args.kwargs["artifacts"], absolute)
                    self.assertIsNone(mocks["build.build"].call_args.kwargs["source"])

    def test_harbor_forwards_reference_cache_never_legacy_image_baseline(self):
        from judge import harbor
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {name: name for name in ("candidate", "projects", "image", "cpuset",
                "cpu_class", "calibration", "upstream_receipt", "pristine", "artifacts")}
            config.update(baseline="/legacy/image-baseline", reference_cache=str(root / "reference-cache"))
            write(root / "config.json", config)
            captured = []
            with patch.object(sys, "argv", ["harbor", "--task", str(root / "task"),
                                           "--config", str(root / "config.json")]), \
                 patch.dict(os.environ, {"JUDGE_LOG_DIR": str(root / "logs")}), \
                 patch.object(harbor.heldout, "check"), \
                 patch.object(harbor, "verify", side_effect=lambda: captured.extend(sys.argv) or 0):
                self.assertEqual(harbor.main(), 0)
            self.assertNotIn("--baseline", captured)
            self.assertNotIn("/legacy/image-baseline", captured)
            self.assertIn("--reference-cache", captured)
            self.assertIn(str(root / "reference-cache"), captured)

    def test_calibration_cannot_be_reused_across_reference_builds(self):
        host = {"sha256": "host", "cpu_class": "cpu"}
        session = {"correct": True, "aa_passed": True, "status": "resolved",
                   "quality": {"noisy": False}, "baseline_build_sha256": "build", "reference_key": "key"}
        receipt = calibration.receipt(session, host, "image", now=10)
        calibration.require(receipt, host, "image", now=11,
                            baseline_build_sha256="build", reference_key="key")
        for change in ({"baseline_build_sha256": "another"}, {"reference_key": "another"}):
            with self.assertRaises(ValueError):
                calibration.require(receipt, host, "image", now=11, **change)

    def test_score_selects_one_reference_for_interfaces_behavior_upstream_and_measurement(self):
        task = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            args = SimpleNamespace(task=task.name, artifacts=root, image="image", out=root / "out",
                cache=root / "cache", cpuset="0-7", agent_host_fingerprint=None,
                separate_sandbox=True, seed=1, minimum=5, maximum=5, half_width=.02, warmups=1)
            execution = {"cpuset": "0-7", "host": {}, "pinning_took_effect": True,
                         "requested_cores": list(range(8)), "available_cores": list(range(8))}
            ref = {"build": root / "rebuilt-reference", "key": "key", "build_sha256": "ref"}
            stack.enter_context(patch("judge.image_recipe.observation",
                                     return_value={"runtime_image_id": "image", "recipe_label_matches": False}))
            patches = {
                "heldout.check": {"status": "unavailable"}, "artifacts": ({}, {}),
                "ensure_image": "image", "score_host.select": execution,
                "score_host.require_separate": {}, "assets": root / "trusted",
                "public_input_preflight.ensure_public_inputs": {"status": "ready"},
                "project_assets": root / "projects", "reference.ensure": ref,
                "build.build": {}, "interfaces.check": {"passed": True}, "behavior.check": {},
                "policy.envelope": {"files": {}, "runtime_configuration_review": []},
                "upstream_gate": (root / "upstream.json", "sha"),
                "evaluate": {"host": {}, "public_only": True, "quality": {}, "protocol": "p", "seed": 1,
                             "candidate_build_sha256": "candidate"},
                "score_result.interpret": {"aa_noise_floor": {}},
            }
            mocks = {name: stack.enter_context(patch("judge.score." + name, return_value=value))
                     for name, value in patches.items()}
            score.run_task(task, args, {})
            for name in ("interfaces.check", "behavior.check", "evaluate"):
                self.assertEqual(mocks[name].call_args.args[1], ref["build"])
            self.assertEqual(mocks["upstream_gate"].call_args.args[-1], ref)
            self.assertEqual(mocks["evaluate"].call_args.kwargs["reference_info"], ref)
