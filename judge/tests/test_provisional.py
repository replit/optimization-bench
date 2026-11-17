import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import score_host, score_result, statistics
from judge.score import artifacts, upstream_gate, project_assets
from judge import gates
from judge.verifier import evaluate, HarnessError


def observation(estimate=.8, ci=(.77, .83), aa=(.99, 1.01)):
    return {"correct": True, "speedup": estimate, "quality": {"noisy": False},
            "cases": {"case": {
                "aa": {"ci95": list(aa), "status": "resolved", "sample_count": 5,
                       "relative_half_width": (aa[1] - aa[0]) / 2},
                "ab": {"ci95": list(ci), "status": "resolved", "sample_count": 5}}}}


class ProvisionalResultTests(unittest.TestCase):
    def test_detects_slowdown_above_noise_floor(self):
        result = score_result.interpret(observation())
        self.assertEqual(result["status"], "slowdown")
        self.assertEqual(result["speedup"], .8)
        self.assertGreater(result["aa_noise_floor"]["case"]["spread_fraction"], 0)

    def test_noop_below_noise_is_inconclusive_not_ratio(self):
        result = score_result.interpret(observation(1, (.995, 1.005)))
        self.assertEqual(result["status"], "inconclusive")
        self.assertIsNone(result["speedup"])

    def test_unresolved_aa_or_noisy_host_suppresses_ratio(self):
        for value in [observation(aa=(.7, 1.4)), observation()]:
            if value["cases"]["case"]["aa"]["ci95"] == [.7, 1.4]:
                value["cases"]["case"]["aa"]["status"] = "not resolved"
            else:
                value["quality"]["noisy"] = True
            self.assertIsNone(score_result.interpret(value)["speedup"])

    def test_aggregators_never_mix_modes_or_raw_measurements(self):
        provisional = {"cpu_class": "A", "correct": True, "speedup": .8,
                       "provisional": True, "claimable": False}
        claimable = {"cpu_class": "B", "correct": True, "speedup": 1.2, "claimable": True}
        for aggregate in (statistics.aggregate_classes, statistics.aggregate_tasks):
            with self.assertRaises(ValueError):
                aggregate([provisional, claimable])
            with self.assertRaises(ValueError):
                aggregate([{**provisional, "diagnostic_only": True}])
            with self.assertRaises(ValueError):
                aggregate([{**provisional, "status": "invalid run", "correct": None}])
            value = aggregate([{**provisional, "speedup": None, "public_only": True}])
            self.assertEqual(value["status"], "inconclusive")
            self.assertTrue(value["provisional"])
            self.assertTrue(value["public_only"])


class ProvisionalHostTests(unittest.TestCase):
    def test_probe_restores_integer_cpu_ids_after_json_transport(self):
        payload = {"counters": {"cpu": {"0": [1, 2, 3]}}}
        step = SimpleNamespace(exit_code=0, stdout=json.dumps(payload).encode(), stderr=b"")
        with patch.object(score_host, "DockerExecutor") as executor:
            executor.return_value.run.return_value = [step]
            result = score_host.probe("image")
        self.assertIn(0, result["counters"]["cpu"])

    def test_pinning_verified_and_no_quota_option_is_added(self):
        initial = {"affinity": list(range(16)), "host": {"boot_id_sha256": "host"}, "cpu_max": "max 100000"}
        pinned = {**initial, "affinity": list(range(8))}
        with patch.object(score_host, "probe", side_effect=[initial, pinned]) as probe:
            result = score_host.select("image")
        self.assertTrue(result["pinning_took_effect"])
        self.assertEqual(probe.call_args.args, ("image", "0,1,2,3,4,5,6,7"))

    def test_unsupported_cpuset_is_explicit_not_fake_pinning(self):
        initial = {"affinity": list(range(16)), "host": {}, "cpu_max": "max 100000"}
        with patch.object(score_host, "probe", side_effect=[initial, HarnessError("cpuset not supported")]):
            result = score_host.select("image")
        self.assertIsNone(result["cpuset"])
        self.assertFalse(result["pinning_took_effect"])
        with patch.object(score_host, "probe", return_value={**initial, "affinity": [0, 1]}):
            with self.assertRaises(HarnessError):
                score_host.select("image")
        with patch.object(score_host, "probe", side_effect=[initial, initial]):
            ignored = score_host.select("image")
        self.assertIsNone(ignored["cpuset"])
        self.assertFalse(ignored["pinning_took_effect"])

    def test_separate_agent_sandbox_is_checked_or_explicitly_attested(self):
        execution = {"host": {"boot_id_sha256": "a" * 64}}
        with self.assertRaises(HarnessError):
            score_host.require_separate(execution, "a" * 64, attested=True)
        with self.assertRaises(HarnessError):
            score_host.require_separate(execution)
        self.assertTrue(score_host.require_separate(execution, "b" * 64)["separate_kernel_verified"])
        self.assertTrue(score_host.require_separate(execution, attested=True)["operator_attested"])
        with self.assertRaises(HarnessError):
            score_host.require_separate(execution, "invalid-fingerprint", attested=True)


class ProvisionalAdmissionTests(unittest.TestCase):
    def test_harbor_roots_with_the_same_basename_use_full_declared_paths(self):
        from judge.score import artifacts
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, trial = root / "task", root / "trial"
            task.mkdir()
            roots = ["first/src", "second/src"]
            config = {"artifacts": [{"source": "/app/example/" + name} for name in roots]}
            (task / "release.json").write_text(json.dumps({
                "source_policy": {"source_roots": roots}}))
            (task / "task.toml").write_text(
                'artifacts = [{source="/app/example/first/src"}, '
                '{source="/app/example/second/src"}]\n')
            for entry in config["artifacts"]:
                path = trial / "artifacts" / (entry.get("destination") or entry["source"].lstrip("/"))
                path.mkdir(parents=True)
                (path / "lib.rs").write_text("pub fn example() {}")
            mapping, _ = artifacts(task, trial)
            self.assertEqual(set(mapping), set(roots))
            self.assertEqual(len(set(mapping.values())), len(roots))

    def test_available_heldout_projects_are_fetched_before_timing_and_keyed_by_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, trusted = root / "task", root / "cache"
            (task / "tests").mkdir(parents=True)
            (task / "tests/heldout.lock.json").write_text('{"status":"available"}')
            (trusted / "projects/public").mkdir(parents=True)
            def fetch(_, destination):
                (destination / "private").mkdir()
            with patch("judge.score.heldout.check") as check, \
                 patch("judge.score.heldout.fetch_archives", side_effect=fetch) as archive:
                target = project_assets(task, trusted, public_only=False)
                self.assertTrue((target / "public").is_dir())
                self.assertTrue((target / "private").is_dir())
                self.assertEqual(project_assets(task, trusted, public_only=False), target)
                self.assertEqual(archive.call_count, 1)
                self.assertEqual(project_assets(task, trusted, public_only=True), trusted / "projects")
                check.assert_called_with(task / "tests", require_available=True)

    def test_upstream_gate_rejects_an_unadmitted_image_baseline(self):
        task = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("judge.score.policy.envelope", return_value={"files": {}}), \
                 patch("judge.score.build_inventory", return_value="build"), \
                 patch.object(gates, "execute_suites",
                              side_effect=gates.UpstreamRejected("pristine tests failed", {})):
                with self.assertRaisesRegex(ValueError, "Admitted rebuilt reference required"):
                    upstream_gate(task, root / "trusted", root / "candidate",
                                  "image", {"cpuset": "0-7"}, root / "out", root / "cache")
            self.assertFalse((root / "out/upstream.json").exists())

    def test_harbor_trial_artifact_paths_and_failed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "task"
            task.mkdir()
            (task / "release.json").write_text(json.dumps({"source_policy": {"source_roots": ["lib"]}}))
            (task / "task.toml").write_text('artifacts = [{source="/app/terser/lib"}]\n')
            trial = root / "trial"
            artifact = trial / "artifacts/app/terser/lib"
            artifact.mkdir(parents=True)
            mapping, _ = artifacts(task, trial)
            self.assertEqual(mapping, {"lib": str(artifact)})
            (trial / "artifacts/manifest.json").write_text(json.dumps([
                {"source": "/app/terser/lib", "status": "failed"}]))
            with self.assertRaises(ValueError):
                artifacts(task, trial)

    def test_public_fallback_keeps_upstream_gate_and_skips_only_registered_calibration(self):
        task = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        build = Path(temporary.name)
        (build / "artifact").write_bytes(b"provisional fixture")
        from judge.verifier import build_inventory
        build_sha = build_inventory(build)
        execution = {"effective_cores": list(range(8)), "requested_cores": list(range(8)),
                     "available_cores": list(range(16)), "pinning_took_effect": True,
                     "host": {"boot_id_sha256": "scorer"}}
        probe = {"host": {"boot_id_sha256": "scorer"}, "counters": {}}
        case = SimpleNamespace(id="batch")
        module = SimpleNamespace(cases=lambda _: [case])
        measured = {"correct": True, "aa_passed": True, "speedup": 1,
                    "cases": {}, "records": [], "status": "resolved"}
        with patch("judge.verifier.behavior.check", return_value={}), \
             patch("judge.verifier.interfaces.check", return_value={"passed": True}), \
             patch("judge.verifier.image_id", return_value="image"), \
             patch("judge.verifier.heldout.check", return_value={"status": "unavailable"}), \
             patch("judge.verifier.references", return_value=[{"path": "case", "expect_len": 0, "expect_sha": "hash"}]), \
             patch("judge.process_state.references", return_value=[{"path": "case", "expect_len": 0, "expect_sha": "hash"}]), \
             patch("judge.verifier.load_task", return_value=module), \
             patch("judge.verifier.scheduler.session", return_value=measured), \
             patch("judge.verifier.build_inventory", return_value=build_sha), \
             patch("judge.verifier.upstream.validate_receipt") as gate, \
             patch("judge.reference.validate_call",
                   return_value={"key": "reference", "build_sha256": build_sha}), \
             patch("judge.verifier.calibration.require") as calibration, \
             patch("judge.verifier.telemetry.quality", return_value={"noisy": False}), \
             patch.object(score_host, "probe", return_value=probe):
            gate.return_value = {"reference_key": "reference", "reference_build_sha256": build_sha}
            result = evaluate(task, build, build, build, "image", "0-7", "unregistered",
                              upstream_receipt=build / "receipt.json", source_sha256="source",
                              provisional=True, execution_host=execution)
        gate.assert_called_once()
        calibration.assert_not_called()
        self.assertTrue(result["public_only"])
        self.assertTrue(result["provisional"])
        self.assertFalse(result["claimable"])
        self.assertEqual(result["upstream_gate"], "passed")
