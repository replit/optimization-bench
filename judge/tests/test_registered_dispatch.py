import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

from judge import host_registry, score, verifier
from judge.verifier import HarnessError


class RegisteredDispatchTests(unittest.TestCase):
    def test_registered_dispatch_prepares_before_timing_and_finalizes_after(self):
        self._dispatch(denied=False)

    def test_denied_registration_preserves_json_result_without_claimability(self):
        self._dispatch(denied=True)

    def test_main_emits_real_verdict_and_nested_denial_evidence(self):
        for noisy, biased in ((False, False), (True, False), (False, True)):
            with self.subTest(noisy=noisy, biased=biased):
                self._dispatch(denied=True, emit=True, noisy=noisy, biased=biased)

    def test_main_still_rejects_unsupported_nested_evidence(self):
        for value in (object(), {"not-a-json-array"}, Path("not-a-json-string")):
            with self.subTest(type=type(value).__name__):
                self._dispatch(denied=True, emit=True, unsupported=value)

    def test_actual_absent_operator_registry_preserves_numeric_result(self):
        for noisy, biased in ((False, False), (True, False), (False, True)):
            with self.subTest(noisy=noisy, biased=biased):
                self._dispatch(denied=True, emit=True, noisy=noisy, biased=biased,
                               actual_registry_missing=True)

    def _dispatch(self, denied, emit=False, noisy=False, biased=False, unsupported=None,
                  actual_registry_missing=False):
        task_name = "sqlglot-mozilla-transpile"
        task = Path(__file__).resolve().parents[2] / "tasks" / task_name
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            # main() scores through by default and reads the upstream receipt for a rejection.
            (root / "upstream.json").write_text("{}")
            args = SimpleNamespace(task=task.name, artifacts=root, image="fixture", out=root / "out",
                cache=root / "cache", cpuset="0-7", agent_host_fingerprint=None,
                separate_sandbox=True, seed=1, minimum=5, maximum=5, half_width=.02, warmups=1,
                gates_only=False, gate_profile="v1.1-rc", session_control=None)
            execution = {"cpuset": "0-7", "host": {}, "pinning_took_effect": True,
                         "requested_cores": list(range(8)), "available_cores": list(range(8))}
            ref = {"build": root / "reference", "key": "fixture-key", "build_sha256": "fixture-ref"}
            cpu = {"timing_performed": False, "records": [
                {"cpu_count": count, "actual_cpus": list(range(count)), "affinity_verified": True,
                 "baseline_cases": 1, "candidate_cases": 1, "passed": True}
                for count in (1, 2, 4, 8)]}
            patches = {
                "heldout.check": {"status": "unavailable"}, "artifacts": ({}, {}),
                "ensure_image": "fixture-image", "score_host.select": execution,
                "public_input_preflight.ensure_public_inputs": {"status": "ready"},
                "score_host.require_separate": {}, "assets": root / "trusted",
                "project_assets": root / "projects", "reference.ensure": ref,
                "policy.repair": {"restored": [], "removed": []},
                "build.build": {}, "interfaces.check": {"passed": True},
                "behavior.check": {"process_state": {"passed": True}},
                "runtime_state.review": {"runtime_configuration_review": []},
                "policy.envelope": {"files": {}, "runtime_configuration_review": []},
                "upstream_gate": (root / "upstream.json", "fixture-source"),
                "fuzzing_generic.check": {"passed": True, "status": "passed", "cpu_correctness": cpu},
                "score_result.interpret": {"aa_noise_floor": {}, "correct": True, "provisional": denied},
            }
            if emit:
                # Keep the actual verdict projection, CLI writer and summary.
                # Build/measurement mocks below are synthetic, not run receipts.
                patches.pop("score_result.interpret")
            for name, value in patches.items():
                stack.enter_context(patch("judge.score." + name, return_value=value))
            stack.enter_context(patch("judge.cpu_correctness.check", return_value=cpu))
            stack.enter_context(patch("judge.image_recipe.observation", return_value={}))
            links = {"fixture": "observed-before-timing"}
            context = SimpleNamespace(cpu_class="fixture-registered", judge_identity={"fixture": True},
                                      as_dict=lambda: {"links": links}, session_control=None)
            denial_links = {"host": {"receipts": [{"sha256": "a" * 64,
                "evidence": {"counts": [0, 30], "ratio": 1.25, "missing": None, "passed": False}}]}}
            if unsupported is not None:
                denial_links["host"]["receipts"][0]["unsupported"] = unsupported
            if actual_registry_missing:
                stack.enter_context(patch.object(score, "ROOT", root))
                stack.enter_context(patch.object(host_registry, "ROOT", root))
                stack.enter_context(patch.object(score, "run",
                    side_effect=lambda supplied, result: score.run_task(task, supplied, result)))
                self.assertFalse((root / "judge/hosts/registry.json").exists())
                prepare = stack.enter_context(patch("judge.host_registry.prepare_registered_session",
                    wraps=host_registry.prepare_registered_session))
            else:
                denial = host_registry.RegistrationError(["fingerprint_unregistered"], denial_links)
                self.assertIsInstance(denial.links, MappingProxyType)
                self.assertIsInstance(denial.links["host"]["receipts"], tuple)
                self.assertIsInstance(denial.links["host"]["receipts"][0]["evidence"], MappingProxyType)
                prepare = stack.enter_context(patch("judge.host_registry.prepare_registered_session",
                    **({"side_effect": denial} if denied else {"return_value": context})))
            finalize = stack.enter_context(patch("judge.host_registry.evaluate_claimability",
                return_value={"claimable": False, "reasons": ["fixture_no_real_qualification"], "links": links}))
            def measure(*args, **kwargs):
                self.assertEqual(kwargs["provisional"], denied)
                if denied:
                    self.assertNotIn("registration_context", kwargs)
                    self.assertFalse((root / "out/registration-before-timing.json").exists())
                else:
                    self.assertIs(kwargs["registration_context"], context)
                    self.assertEqual(args[6], "fixture-registered")
                    self.assertTrue((root / "out/registration-before-timing.json").exists())
                finalize.assert_not_called()
                measurement = {"host": {}, "public_only": True, "quality": {}, "protocol": "fixture",
                        "seed": 1, "candidate_build_sha256": "fixture-candidate", "correct": True,
                        "provisional": kwargs["provisional"]}
                if emit:
                    units = ("batch", "optimizer", "lineage", "diff")
                    measurement.update(speedup=1.25, aa_passed=not biased,
                        wall_statistics={"ci95": [1.2, 1.3]},
                        quality={"noisy": noisy, "missing_cpu_counters": False,
                                 "frequency_before": {"0": 2300000}},
                        cases={name: {
                            "aa": {"ci95": [1.031, 1.032] if biased else [.999, 1.001],
                                   "relative_half_width": .001, "sample_count": 30, "status": "resolved"},
                            "ab": {"ci95": [1.2, 1.3], "relative_half_width": .04,
                                   "sample_count": 30, "status": "resolved"},
                        } for name in sorted(units)})
                return measurement
            stack.enter_context(patch.object(score, "evaluate", side_effect=measure))
            path = root / "out/result.json"
            if emit:
                argv = ["judge.score", "--task", task.name, "--artifacts", str(root),
                        "--out", str(args.out), "--cache", str(args.cache),
                        "--image", "fixture", "--cpuset", "0-7", "--separate-sandbox",
                        "--seed", "1", "--minimum", "5", "--maximum", "5", "--warmups", "1"]
                stack.enter_context(patch("sys.argv", argv))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                if unsupported is not None:
                    with self.assertRaisesRegex(TypeError, "not JSON serializable"):
                        score.main()
                    self.assertTrue((args.out / "measurements.json").is_file())
                    self.assertFalse(path.exists())
                    self.assertFalse((args.out / "summary.txt").exists())
                    self.assertEqual(output.getvalue(), "")
                    finalize.assert_not_called()
                    return
                self.assertEqual(score.main(), 0)
                result = json.loads(path.read_text())
                measurement = json.loads((args.out / "measurements.json").read_text())
                expected = score.score_result.interpret(measurement)
                for name, value in expected.items():
                    self.assertEqual(result[name], value, name)
                self.assertEqual(result["quality"], measurement["quality"])
                self.assertEqual(len(result["cases"]), 4)
                self.assertEqual(measurement["aa_passed"], not biased)
                self.assertEqual(result["status"], "inconclusive" if noisy or biased else "speedup")
                self.assertEqual(result["speedup"], None if noisy or biased else 1.25)
                self.assertEqual(result["optim_score"], 1.0 if noisy or biased else 1.2)
                self.assertEqual((args.out / "summary.txt").read_text(),
                                 score.score_result.summary(result))
                self.assertIn("PROVISIONAL — NOT CLAIMABLE", output.getvalue())
            else:
                result = score.run_task(task, args, {})
            prepare.assert_called_once()
            self.assertFalse(result["claimable"])
            if denied:
                finalize.assert_not_called()
                self.assertTrue(result["provisional"])
                if not emit:
                    score.save(path, result)
                restored = json.loads(path.read_text())
                attempt = restored["registration_attempt"]
                self.assertFalse(attempt["eligible_to_measure_registered"])
                self.assertEqual(attempt["reasons"],
                                 ["registry_invalid"] if actual_registry_missing else ["fingerprint_unregistered"])
                if actual_registry_missing:
                    self.assertEqual(prepare.call_args.args[1], root / "judge/hosts/registry.json")
                    self.assertFalse((root / "judge/hosts/registry.json").exists())
                    self.assertEqual(attempt["links"], {})
                else:
                    self.assertEqual(attempt["links"], denial_links)
                    attempt["links"]["host"]["receipts"][0]["sha256"] = "b" * 64
                    self.assertEqual(denial.links["host"]["receipts"][0]["sha256"], "a" * 64)
            else:
                finalize.assert_called_once()
                self.assertEqual(result["mode"], "registered")
                self.assertFalse(result["provisional"])

    def test_verifier_rejects_unprepared_metadata_as_registration_authority(self):
        with self.assertRaisesRegex(HarnessError, "prepared context"):
            verifier.evaluate(Path("."), Path("."), Path("."), Path("."), "fixture", "0-7",
                              "fixture", registration_context=SimpleNamespace(), provisional=False)
