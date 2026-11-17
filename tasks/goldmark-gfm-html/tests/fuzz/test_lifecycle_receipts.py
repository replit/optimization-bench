"""Validate retained actual calibration facts; not a replacement for execution."""
import gzip
import hashlib
import json
from pathlib import Path
import unittest


class LifecycleReceiptTests(unittest.TestCase):
    def test_actual_joined_and_leaked_profile_controls(self):
        path = Path(__file__).parent/"receipts/go-lifecycle/controls.json.gz"
        facts = json.loads(gzip.decompress(path.read_bytes()))
        self.assertEqual(facts["lifecycle_policy"],
                         {"kind":"go-goroutines-v1","native_extensions":False,
                          "candidate_module_prefixes":["github.com/yuin/goldmark/v2"]})
        groups = facts["observations"]
        for arm in ("baseline", "joined", "leaked"):
            for mode in ("fresh", "persistent"):
                for observation in groups[arm][mode]:
                    self.assertEqual(observation["diagnostics"]["execution_affinity"]["cpus"],
                                     list(range(8)))
                    raw = observation["diagnostics"]["call_lifecycle"]
                    for point in ("before", "after"):
                        self.assertEqual(raw[point]["threads"],
                                         len(raw[point]["thread_identities"]))
                        self.assertGreater(raw[point]["gomaxprocs"], 0)
                    after = raw["after"]
                    self.assertTrue(after["goroutine_profile_available"])
                    self.assertEqual(hashlib.sha256(after["goroutine_profile_raw"].encode()).hexdigest(),
                                     after["goroutine_profile_sha256"])
                    if arm == "leaked":
                        self.assertGreater(after["goroutines"], 1)
                        self.assertTrue(after["candidate_goroutine_frames"])
                        self.assertTrue(any("created by " in block
                                            for block in after["candidate_goroutine_frames"]))
                    else:
                        self.assertEqual(after["goroutines"], 1)
                        self.assertEqual(after["candidate_goroutine_frames"], [])
        self.assertEqual([value["diagnostics"]["call_lifecycle"]["after"]["goroutines"]
                          for value in groups["leaked"]["persistent"]], [3, 5, 7])
