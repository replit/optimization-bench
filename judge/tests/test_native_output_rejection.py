import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import process_state, reference, score, verifier
from runner import HarnessError, StepResult, canonical_json, fingerprint


class NativeOutputRejectionTests(unittest.TestCase):
    def mismatch(self):
        return process_state.NativeOutputMismatch({"case": (1, "a" * 64)},
                                                 {"case": (2, "b" * 64)})

    def baseline(self):
        return process_state.ObservedState({"threads": 1}, {}, "baseline-fixture")

    def test_normal_exit_regression_has_a_typed_semantic_mismatch(self):
        def collect(case, context, results):
            return {"case": canonical_json({"format": "cli-output-v1",
                                           "runs": [result.streams() for result in results]})}

        module = SimpleNamespace(collect=collect)
        expected = {"case": fingerprint(collect(None, None, [StepResult(0, b"[]", b"")])["case"])}
        results = [StepResult(0, b"", b""),
                   StepResult(42, b"", b"", telemetry={"execution_protocol": "process"})]
        with self.assertRaises(process_state.NativeOutputMismatch) as caught:
            process_state.validate_native(module, None, None, [1, 2], results, expected)
        self.assertEqual(caught.exception.expected, expected)
        self.assertNotEqual(caught.exception.observed, expected)
        self.assertNotIsInstance(caught.exception, process_state.CandidateOutputRejected)

    def test_only_candidate_output_mismatch_after_checked_baseline_is_rejected(self):
        mismatch = self.mismatch()
        with patch.object(process_state, "probe", side_effect=[self.baseline(), mismatch]) as probe:
            with self.assertRaises(process_state.CandidateOutputRejected) as caught:
                process_state.check(Path("fixture"), Path("base"), Path("candidate"), "image", "0")
        self.assertEqual(probe.call_count, 2)
        self.assertIs(caught.exception.__cause__, mismatch)
        self.assertEqual(caught.exception.report["arm"], "candidate")
        self.assertTrue(caught.exception.report["baseline_output_checked"])
        self.assertEqual(caught.exception.report["observed"], mismatch.observed)

    def test_baseline_output_failure_is_not_candidate_rejection(self):
        mismatch = self.mismatch()
        with patch.object(process_state, "probe", side_effect=mismatch) as probe:
            with self.assertRaises(process_state.NativeOutputMismatch) as caught:
                process_state.check(Path("fixture"), Path("base"), Path("candidate"), "image", "0")
        self.assertEqual(probe.call_count, 1)
        self.assertIs(caught.exception, mismatch)

    def test_unconfigured_baseline_does_not_qualify_a_candidate_rejection(self):
        mismatch = self.mismatch()
        with patch.object(process_state, "probe",
                          side_effect=[process_state.not_configured("fixture"), mismatch]):
            with self.assertRaises(process_state.NativeOutputMismatch) as caught:
                process_state.check(Path("fixture"), Path("base"), Path("candidate"), "image", "0")
        self.assertIs(caught.exception, mismatch)

    def test_candidate_metadata_observer_protocol_and_harness_errors_are_not_translated(self):
        for error in (ValueError("missing protected references"), ValueError("observer setup failed"),
                      ValueError("execution protocol incomplete"), HarnessError("transport failed")):
            with self.subTest(error=str(error)), \
                    patch.object(process_state, "probe", side_effect=[self.baseline(), error]):
                with self.assertRaises(type(error)) as caught:
                    process_state.check(Path("fixture"), Path("base"), Path("candidate"), "image", "0")
                self.assertIs(caught.exception, error)

    def test_equal_state_and_persistent_worker_decisions_are_unchanged(self):
        for threads, status in ((1, "passed"), (2, "needs_review")):
            right = process_state.ObservedState({"threads": threads}, {}, "candidate-fixture")
            with self.subTest(threads=threads), \
                    patch.object(process_state, "probe", side_effect=[self.baseline(), right]):
                result = process_state.check(Path("fixture"), Path("base"), Path("candidate"), "image", "0")
            self.assertEqual(result["status"], status)
            self.assertEqual(result["passed"], threads == 1)

    def test_score_cli_keeps_candidate_and_baseline_semantics_separate(self):
        for candidate in (True, False):
            error = process_state.CandidateOutputRejected(self.mismatch()) if candidate else self.mismatch()
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "out"
                argv = ["score", "--task", "fixture", "--artifacts", "fixture.json", "--out", str(out)]
                with patch("sys.argv", argv), patch.object(score, "run", side_effect=error), \
                        contextlib.redirect_stdout(io.StringIO()):
                    code = score.main()
                result = json.loads((out / "result.json").read_text())
                self.assertEqual(code, 1 if candidate else 2)
                self.assertEqual(result["status"], "incorrect" if candidate else "invalid run")
                self.assertIs(result["correct"], False if candidate else None)
                receipt = out / "native-output-rejection.json"
                self.assertEqual(receipt.exists(), candidate)
                if candidate:
                    self.assertEqual(json.loads(receipt.read_text()), result["native_output_rejection"])
                    self.assertFalse(result["timing_performed"])
                    self.assertFalse(result["aa_sampling_performed"])
                    self.assertIsNone(result["speedup"])

    def test_direct_cli_preserves_typed_receipt_and_invalid_prerequisites(self):
        for error in (process_state.CandidateOutputRejected(self.mismatch()), self.mismatch(),
                      reference.QualificationError("pristine failure"),
                      HarnessError("execution metadata unavailable"), ValueError("bad metadata")):
            candidate = isinstance(error, process_state.CandidateOutputRejected)
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "result.json"
                task = root / "task"
                task.mkdir()
                (task / "release.json").write_text(json.dumps({"source_policy": {}}))
                argv = ["verifier", "--task", str(task), "--baseline", str(root / "base"),
                        "--candidate", str(root / "candidate"), "--projects", str(root / "projects"),
                        "--image", "fixture", "--cpuset", "0", "--cpu-class", "fixture",
                        "--pristine", str(root / "pristine"), "--source", str(root / "source"),
                        "--output", str(output)]
                with patch("sys.argv", argv), patch.object(verifier, "image_id", return_value="fixture"), \
                        patch.object(verifier.policy, "envelope", return_value={"files": {}}), \
                        patch.object(score, "assets", return_value=root / "trusted"), \
                        patch("judge.public_input_preflight.ensure_public_inputs"), \
                        patch.object(reference, "ensure", return_value={"build": root / "base"}), \
                        patch.object(verifier, "evaluate", side_effect=error):
                    code = verifier.main()
                result = json.loads(output.read_text())
                self.assertEqual(code, 1 if candidate else 2)
                self.assertEqual(result["status"], "incorrect" if candidate else "invalid run")
                receipt = root / "result-native-output-rejection.json"
                self.assertEqual(receipt.exists(), candidate)
                self.assertFalse(result["claimable"])
                if candidate:
                    self.assertIs(result["correct"], False)
                    self.assertEqual(json.loads(receipt.read_text()), result["native_output_rejection"])
                    self.assertFalse(result["timing_performed"])
                    self.assertFalse(result["aa_sampling_performed"])
