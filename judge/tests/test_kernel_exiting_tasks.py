"""PF_EXITING is a kernel lifetime fact, not a thread-name exemption."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
import shim


def stat_line(pid, flags, state="R"):
    fields = ["1"] * 20
    fields[0], fields[6], fields[19] = state, str(flags), "100"
    return str(pid) + " (fixture) " + " ".join(fields)


class KernelExitTests(unittest.TestCase):
    def test_joined_kernel_exit_is_diagnostic_but_live_detached_worker_is_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / "8"
            for tid in (8, 9):
                (proc / "task" / str(tid)).mkdir(parents=True)
            (proc / "stat").write_text(stat_line(8, 0, "T"))
            (proc / "cmdline").write_bytes(b"fixture\0")
            (proc / "task/8/stat").write_text(stat_line(8, 0))
            for state in ("R", "T"):
                (proc / "task/9/stat").write_text(stat_line(9, 4, state))
                exiting = []
                value = shim.leftover_snapshot(proc, exiting)
                self.assertEqual(value["threads"], 1)
                self.assertEqual([x["tid"] for x in value["thread_identities"]], [8])
                self.assertEqual(exiting[0]["tid"], 9)
                self.assertEqual(exiting[0]["flags"] & 4, 4)
            (proc / "task/9/stat").write_text(stat_line(9, 0, "S"))
            value = shim.leftover_snapshot(proc)
            self.assertEqual(value["threads"], 2)  # No allowance for a live user worker.
            (proc / "task/8/stat").write_text(stat_line(8, 4, "R"))
            (proc / "task/9/stat").write_text(stat_line(9, 4, "T"))
            exiting = []
            self.assertIsNone(shim.leftover_snapshot(proc, exiting))
            self.assertEqual(len(exiting), 2)


if __name__ == "__main__":
    unittest.main()
