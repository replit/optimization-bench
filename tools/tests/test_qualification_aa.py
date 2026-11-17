"""The qualification sampler never pads units with repeated components."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qualify_aa import sample_units, qualification_passed, startup_share, reference_identity
from judge.verifier import build_inventory
import qualify_aa


class QualificationAaTests(unittest.TestCase):
    def test_tantivy_public_preparation_precedes_measurement(self):
        task = Path("/fixture/tantivy-gharchive-single-segment-indexing")
        events = []
        with patch.object(qualify_aa.public_input_preflight, "ensure_public_inputs",
                          side_effect=lambda *a, **k: events.append("fetch")) as fetch, \
                patch.object(qualify_aa.subprocess, "run",
                             side_effect=lambda *a, **k: events.append("prepare")) as prepare:
            with qualify_aa.prepared_task(task, "public") as view:
                self.assertEqual(events, ["fetch", "prepare"])
                self.assertEqual(view, task)
        fetch.assert_called_once_with(task, fetch=True)
        prepare.assert_called_once_with(
            [sys.executable, str(task / "environment/prepare.py"), str(task / "environment")],
            check=True)

    def test_failed_public_preparation_never_yields_to_measurement(self):
        task = Path("/fixture/tantivy-gharchive-single-segment-indexing")
        with patch.object(qualify_aa.public_input_preflight, "ensure_public_inputs"), \
                patch.object(qualify_aa.subprocess, "run", side_effect=ValueError("bad payload")):
            with self.assertRaisesRegex(ValueError, "bad payload"):
                with qualify_aa.prepared_task(task, "public"):
                    self.fail("measurement must not start")

    def test_reference_receipt_binds_rebuilt_build_and_discloses_image_difference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, rebuilt = root / "image", root / "rebuilt"
            original.mkdir()
            rebuilt.mkdir()
            (original / "artifact").write_bytes(b"image")
            (rebuilt / "artifact").write_bytes(b"rebuilt")
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({"task": "task", "image": "image-id",
                                          "build_sha256": build_inventory(rebuilt),
                                          "source_sha256": "source-id"}))
            result = reference_identity(Path("task"), "image-id", rebuilt, original, receipt)
            self.assertEqual(result["image_vs_rebuilt_identity"], "different")
            self.assertFalse(result["image_artifact_is_timed"])
            (rebuilt / "artifact").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "commitment mismatch"):
                reference_identity(Path("task"), "image-id", rebuilt, original, receipt)
            with self.assertRaisesRegex(ValueError, "supplied together"):
                reference_identity(Path("task"), "image-id", rebuilt, original)

    def test_distinct_components_once_per_arm_and_every_sample_retained(self):
        calls, records = [], []
        def measure(case, arm):
            calls.append((case, arm))
            return {"correct": True, "ns": 500_000_000}
        result = sample_units({"unit": ["a", "b"]}, measure, records, maximum=5)
        self.assertEqual(len(records), 12)  # One warmup pair and five measured pairs.
        self.assertEqual(len(calls), 24)
        self.assertTrue(all([c["case"] for c in r["components"]] == ["a", "b"] for r in records))
        first_lanes = [row["arm"] for row in records[2::2]]
        self.assertLessEqual(abs(first_lanes.count("a") - first_lanes.count("b")), 1)
        self.assertEqual(result["unit"]["sample_count"], 5)
        self.assertTrue(qualification_passed({"unit": ["a", "b"]}, result, {"noisy": False}, None))
        self.assertFalse(qualification_passed({"unit": ["a", "b"]}, result, {"noisy": True}, None))

    def test_short_unit_fails_instead_of_repeating(self):
        records = []
        with self.assertRaisesRegex(ValueError, "below 1 second"):
            sample_units({"unit": ["a"]}, lambda case, arm: {"correct": True, "ns": 1}, records)
        self.assertEqual(len(records), 1)

    def test_mismatch_fails_and_loose_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "output mismatch"):
            sample_units({"unit": ["a"]}, lambda case, arm: {
                "correct": False, "ns": 1_000_000_000}, [])
        for extra in ({"minimum_seconds": .9}, {"spread_pp": 5.1}, {"warmups": 0}):
            with self.assertRaises(ValueError):
                sample_units({"unit": ["a"]}, lambda *args: None, [], **extra)

    def test_bias_is_not_a_pass_even_with_narrow_interval(self):
        with patch("qualify_aa.stopping", return_value={
                "ci95": [1.01, 1.02], "status": "resolved", "stop": True}):
            result = sample_units({"unit": ["a"]}, lambda case, arm: {
                "correct": True, "ns": 1_000_000_000}, [], maximum=5)
        self.assertFalse(qualification_passed({"unit": ["a"]}, result, {"noisy": False}, None))

    def test_startup_share_uses_medians_not_sums(self):
        result = startup_share([1, 2, 100], [100, 200, 10000])
        self.assertEqual(result["share_fraction"], .01)
        self.assertEqual(result["median_process_wall_ns"], 2)
        self.assertEqual(result["median_unit_wall_ns"], 200)

    def test_startup_share_accounts_for_all_native_processes_in_unit(self):
        result = startup_share([1, 2, 100], [100, 200, 10000], 96)
        self.assertEqual(result["processes_per_unit"], 96)
        self.assertEqual(result["estimated_unit_startup_ns"], 192)
        self.assertEqual(result["share_fraction"], .96)
        for count in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                startup_share([2], [200], count)

    def test_startup_review_callback_stops_before_timed_pairs(self):
        records = []
        def reject(unit, times):
            self.assertEqual(times, [1_000_000_000, 1_000_000_000])
            raise ValueError("Startup share exceeds 2%")
        with self.assertRaisesRegex(ValueError, "exceeds 2%"):
            sample_units({"unit": ["a"]}, lambda *args: {
                "correct": True, "ns": 1_000_000_000}, records, before_timed=reject)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row["phase"] == "warmup" for row in records))
