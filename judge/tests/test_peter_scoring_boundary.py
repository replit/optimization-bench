"""The format migration must not silently replace family/control scoring plans."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from judge.verifier import evaluate


class ScoringBoundaryTests(unittest.TestCase):
    def test_unqualified_task_cannot_enter_statistical_or_scored_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "tests").mkdir()
            (task / "tests/judge.json").write_text(json.dumps({
                "public_dry_run_only": True, "scoring_blocker": "original plan not requalified"}))
            with patch("judge.verifier.telemetry.pinned", return_value=list(range(8))), \
                    patch("judge.verifier.telemetry.fingerprint", return_value={}), \
                    patch("judge.verifier.image_id", return_value="test-image"):
                for plumbing in (True, False):
                    with self.subTest(plumbing=plumbing), self.assertRaisesRegex(ValueError, "not requalified"):
                        evaluate(task, task, task, task, "test-image", "0-7", "sandbox",
                                 plumbing=plumbing)
