"""Managed-runtime ownership is not a blanket OS-thread exemption."""
import copy
import hashlib
import unittest

from judge import fuzzing_lifecycle as policy


def snapshot(threads, leak=False):
    profile = "goroutine 1 [running]:\nmain.observe()\n"
    if leak:
        profile += "\ngoroutine 2 [chan receive]:\nexample/library.background()\ncreated by example/library.call\n"
    return {"threads": threads, "goroutines": 2 if leak else 1, "gomaxprocs": 4,
            "thread_identities": [{"tid": 100+i, "flags": 0} for i in range(threads)],
            "goroutine_profile_raw": profile, "goroutine_profile_available": True,
            "goroutine_profile_sha256": hashlib.sha256(profile.encode()).hexdigest()}


class ManagedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"kind": "go-goroutines-v1", "native_extensions": False,
                       "candidate_module_prefixes": ["example/library"]}
        self.row = {arm: {"before": snapshot(2), "after": snapshot(2)}
                    for arm in ("baseline", "candidate")}

    def test_joined_go_work_with_idle_runtime_ms_passes(self):
        self.row["candidate"]["after"] = snapshot(7)
        self.assertEqual(policy.differences(self.row, self.policy), [])

    def test_goroutine_retained_in_candidate_code_fails(self):
        self.row["candidate"]["after"] = snapshot(3, leak=True)
        self.assertIn("live_go_goroutines", policy.differences(self.row, self.policy))

    def test_profile_missing_tampered_or_incomplete_is_unmeasured(self):
        for change in ({"goroutine_profile_available": False}, {"goroutine_profile_sha256": "0"*64},
                       {"goroutines": 2}):
            row = copy.deepcopy(self.row)
            row["candidate"]["after"].update(change)
            self.assertIsNone(policy.differences(row, self.policy))

    def test_os_allowance_and_gomaxprocs_are_not_task_overrides(self):
        self.row["candidate"]["after"] = snapshot(9)
        self.policy["os_thread_allowance"] = 1000
        self.assertIn("go_os_thread_allowance_exceeded", policy.differences(self.row, self.policy))
        self.row["candidate"]["after"]["gomaxprocs"] = 100
        self.assertIn("gomaxprocs_changed", policy.differences(self.row, self.policy))

    def test_native_stays_native_and_other_managed_owners_need_measurement(self):
        self.assertEqual(policy.differences(self.row, {"kind": "native-os-threads"}), [])
        self.assertIsNone(policy.differences(self.row, {"kind": "node-libuv"}))
        self.policy["native_extensions"] = True
        self.assertIsNone(policy.differences(self.row, self.policy))


if __name__ == "__main__":
    unittest.main()
