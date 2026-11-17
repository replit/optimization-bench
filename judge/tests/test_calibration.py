import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from judge import calibration

ROOT = Path(__file__).resolve().parents[2]


class CalibrationGateTests(unittest.TestCase):
    def test_early_gate_result_is_rejected_before_host_lookup(self):
        for status in ("needs_review", "interface_broken", "differential_mismatch",
                       "not_configured", "invalid run"):
            with self.subTest(status=status):
                early = {"status": status, "baseline_build_sha256": "same",
                         "candidate_build_sha256": "same", "claimable": False}
                with self.assertRaisesRegex(ValueError, "no calibration performed: gate result " + status):
                    calibration.from_session(early)
                with self.assertRaisesRegex(ValueError, "no calibration performed: gate result " + status):
                    calibration.receipt(early, {}, "image")

    def test_cli_gate_failure_is_clear_and_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session, output = root / "session.json", root / "calibration.json"
            session.write_text(json.dumps({"status": "needs_review",
                "baseline_build_sha256": "same", "candidate_build_sha256": "same"}))
            result = subprocess.run([sys.executable, "-m", "judge.calibration",
                                     str(session), "--output", str(output)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2)
            self.assertIn("no calibration performed: gate result needs_review", result.stderr)
            self.assertNotIn("KeyError", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_completed_but_unresolved_or_noisy_aa_stays_nonpassing(self):
        session = {"status": "resolved", "correct": True, "aa_passed": True,
                   "quality": {"noisy": False}, "baseline_build_sha256": "same",
                   "candidate_build_sha256": "same", "host": {"sha256": "host", "cpu_class": "A"},
                   "image": "image"}
        self.assertTrue(calibration.from_session(session, now=1)["passed"])
        for change in ({"status": "not resolved"}, {"aa_passed": False},
                       {"quality": {"noisy": True}}):
            self.assertFalse(calibration.from_session({**session, **change}, now=1)["passed"])
        with self.assertRaisesRegex(ValueError, "incomplete measurement host/image"):
            calibration.from_session({**session, "host": None})
        with self.assertRaisesRegex(ValueError, "identical baseline builds"):
            calibration.from_session({**session, "candidate_build_sha256": "other"})
