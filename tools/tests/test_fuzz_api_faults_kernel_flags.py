import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.fuzz_api_faults.lifecycle import (
    PF_EXITING, _live_threads, _stat_identity, snapshot, validate_snapshot,
)


def stat(tid, state="R", flags=4194368):
    fields = ["0"] * 22
    fields[0], fields[1], fields[6], fields[19] = state, "100", str(flags), str(tid + 10)
    return f"{tid} (name with ) parentheses) " + " ".join(fields) + "\n"


class KernelFlagsTests(unittest.TestCase):
    def test_flags_are_field_nine_not_state_or_thread_name(self):
        row = _stat_identity(stat(42, flags=4194380))
        self.assertEqual(row["flags"], 0x40004c)
        self.assertEqual(row["start_ticks"], 52)
        self.assertTrue(row["flags"] & PF_EXITING)
        with self.assertRaises(ValueError):
            _stat_identity(stat(42, flags=-1))

    def test_exiting_running_task_is_retained_only_as_diagnostic(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for tid, state, flags in (
                (1, "R", 4194368), (2, "R", 4194380),
                (3, "S", 4194368), (4, "D", 4194368), (5, "Z", 0),
            ):
                task = root / "task" / str(tid)
                task.mkdir(parents=True)
                (task / "stat").write_text(stat(tid, state, flags))
                (task / "status").write_text("Tgid:\t100\n")
            excluded = []
            rows = _live_threads(root, 100, excluded)
            self.assertEqual([r["tid"] for r in rows], [1, 3, 4])
            self.assertEqual(len(excluded), 1)
            self.assertEqual(excluded[0], {
                "tid": 2, "start_ticks": 12, "state": "R", "flags": 4194380,
                "tgid": None, "scope_pid": 100,
            })

    def test_second_stat_read_can_witness_exit_transition(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for tid in (1, 2):
                task = root / "task" / str(tid)
                task.mkdir(parents=True)
                (task / "stat").write_text(stat(tid))
                (task / "status").write_text("Tgid:\t100\n")
            original = Path.read_text
            reads = 0
            def read(path, *args, **kwargs):
                nonlocal reads
                if path == root / "task/2/stat":
                    reads += 1
                    return stat(2, flags=4194380 if reads == 2 else 4194368)
                return original(path, *args, **kwargs)
            excluded = []
            with patch.object(Path, "read_text", read):
                rows = _live_threads(root, 100, excluded)
            self.assertEqual([r["tid"] for r in rows], [1])
            self.assertEqual(excluded[0]["flags"], 4194380)
            self.assertEqual(excluded[0]["tgid"], 100)

    def test_missing_flags_pending_but_exiting_live_identity_invalid(self):
        facts = snapshot()
        self.assertTrue(validate_snapshot(facts)["complete"])
        self.assertIn("excluded_exiting_threads", facts)
        self.assertTrue(all(not r["flags"] & PF_EXITING for r in facts["thread_identities"]))
        bad = copy.deepcopy(facts)
        bad["thread_identities"][0]["flags"] |= PF_EXITING
        with self.assertRaises(ValueError):
            validate_snapshot(bad)
        missing = copy.deepcopy(facts)
        del missing["thread_identities"][0]["flags"]
        self.assertEqual(validate_snapshot(missing),
                         {"complete": False, "reason": "thread_flags_unavailable"})
        missing["counts"]["threads"] += 1
        with self.assertRaises(ValueError):
            validate_snapshot(missing)


if __name__ == "__main__":
    unittest.main()
