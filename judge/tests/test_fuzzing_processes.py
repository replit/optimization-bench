import unittest

from judge.fuzzing_processes import classify_pairs, runtime_helper


def tracker(fd=3):
    return {"pid": 10, "cmdline": "/usr/local/bin/python3.14 -c from multiprocessing.resource_tracker import main;main(" + str(fd) + ")"}


class RuntimeHelperTests(unittest.TestCase):
    def test_exact_stdlib_identity_not_a_name_substring(self):
        self.assertEqual(runtime_helper(tracker(3)), runtime_helper(tracker(9)))
        self.assertIsNone(runtime_helper({"cmdline": "fake-python -c from multiprocessing.resource_tracker import main;main(3)"}))
        self.assertIsNone(runtime_helper({"cmdline": "python3 -c print('multiprocessing.resource_tracker')"}))
        self.assertIsNone(runtime_helper({"cmdline": "python3 -c from multiprocessing.resource_tracker import main;main(3);print('user code')"}))

    def test_matching_pristine_helpers_report_only(self):
        pairs = [{"baseline": {"stopped_descendants": [tracker(3)]},
                  "candidate": {"stopped_descendants": [tracker(7)]}} for _ in range(2)]
        self.assertEqual(classify_pairs(pairs)["status"], "passed")

    def test_intermittent_pristine_tracker_not_candidate_failure(self):
        pairs = [{"baseline": {"stopped_descendants": []}, "candidate": {"stopped_descendants": [tracker()]}},
                 {"baseline": {"stopped_descendants": [tracker()]}, "candidate": {"stopped_descendants": []}}]
        self.assertEqual(classify_pairs(pairs)["status"], "baseline_nonrepeatable")
        self.assertEqual(classify_pairs(pairs[:1], identical_pristine=True)["status"], "baseline_nonrepeatable")

    def test_pool_or_other_user_worker_stays_blocking(self):
        pool = {"pid": 22, "cmdline": "python3 -c from multiprocessing.spawn import spawn_main;spawn_main(tracker_fd=3,pipe_handle=8)"}
        pairs = [{"baseline": {"stopped_descendants": []},
                  "candidate": {"stopped_descendants": [pool]}} for _ in range(2)]
        self.assertIsNone(runtime_helper(pool))
        self.assertEqual(classify_pairs(pairs)["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
