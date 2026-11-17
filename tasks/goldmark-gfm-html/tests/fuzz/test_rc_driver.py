"""Task-native affinity transport must preserve evidence for strict CPU checks."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from judge.cpu_correctness import InvalidEvidence, MissingEvidence, _affinity


spec = importlib.util.spec_from_file_location(
    "goldmark_rc_driver", Path(__file__).with_name("rc_driver.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class AffinityTransportTests(unittest.TestCase):
    def observation(self, receipt):
        return {"effects": {"unchanged": True},
                "diagnostics": {"execution_affinity": receipt, "other": "preserved"}}

    def decode(self, value):
        return driver._observations(json.dumps(value).encode())[0]

    def test_observed_cpu_list_and_complete_native_receipt_are_retained(self):
        receipt = {"available": True, "method": "sched_getaffinity",
                   "pid": 73, "cpus": [2, 5, 7]}
        original = self.observation(receipt)
        expected = copy.deepcopy(original)
        expected["diagnostics"]["native_execution_affinity"] = receipt
        expected["diagnostics"]["execution_affinity"] = receipt["cpus"]
        actual = self.decode(original)
        self.assertEqual(actual, expected)
        self.assertEqual(_affinity(actual["diagnostics"]["execution_affinity"], [2, 5, 7]),
                         [2, 5, 7])

    def test_unavailable_or_unproven_affinity_remains_missing_evidence(self):
        for available in (False, None, 0, 1, "true"):
            with self.subTest(available=available):
                receipt = {"available": available, "method": "sched_getaffinity",
                           "errno": 22, "cpus": [2, 5, 7]}
                actual = self.decode(self.observation(receipt))["diagnostics"]
                self.assertEqual(actual["native_execution_affinity"], receipt)
                self.assertIsNone(actual["execution_affinity"])
                with self.assertRaises(MissingEvidence):
                    _affinity(actual["execution_affinity"], [2, 5, 7])

    def test_actual_mismatched_masks_still_fail_strict_validation(self):
        for cpus in ([2, 5], [0, 1, 2], [2, 5, 5]):
            with self.subTest(cpus=cpus):
                receipt = {"available": True, "method": "sched_getaffinity",
                           "pid": 73, "cpus": cpus}
                actual = self.decode(self.observation(receipt))["diagnostics"]
                self.assertEqual(actual["native_execution_affinity"], receipt)
                self.assertEqual(actual["execution_affinity"], cpus)
                with self.assertRaises(InvalidEvidence):
                    _affinity(actual["execution_affinity"], [2, 5, 7])

    def test_missing_or_malformed_native_receipt_cannot_supply_affinity(self):
        receipts = [None, [], "2,5,7", {"available": True}, {"cpus": [2, 5, 7]}]
        receipts.extend({"available": True, "cpus": cpus}
                        for cpus in (None, [], [2, 5, True], ["2", 5, 7],
                                     {"cpus": [2, 5, 7]}, [-1, 5, 7]))
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                actual = self.decode(self.observation(receipt))["diagnostics"]
                self.assertEqual(actual["native_execution_affinity"], receipt)
                self.assertIsNone(actual["execution_affinity"])
                with self.assertRaises(MissingEvidence):
                    _affinity(actual["execution_affinity"], [2, 5, 7])

    def test_missing_affinity_does_not_discard_native_panic_diagnostics(self):
        actual = self.decode({"termination": {"kind": "panic"},
                              "diagnostics": {"panic_text": "native panic"}})
        self.assertEqual(actual["termination"], {"kind": "panic"})
        self.assertEqual(actual["diagnostics"],
                         {"panic_text": "native panic", "execution_affinity": None,
                          "native_execution_affinity": None})
        with self.assertRaises(MissingEvidence):
            _affinity(actual["diagnostics"]["execution_affinity"], [2, 5, 7])
