"""Exit clocks follow kernel notifications, not timeout polling quanta."""
import errno
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))
import runner


class ExitTimerTests(unittest.TestCase):
    def child(self, program):
        child = subprocess.Popen([sys.executable, "-c", program],
                                 stdout=subprocess.PIPE, start_new_session=True)
        def cleanup():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            child.stdout.close()
        self.addCleanup(cleanup)
        return child

    def test_sleep_exit_observation_has_no_fifty_ms_quantization(self):
        for delay in (.060, .080, .110):
            lags = []
            for _ in range(5):
                child = self.child(
                    f"import time,os;time.sleep({delay});"
                    "os.write(1,str(time.perf_counter_ns()).encode());os._exit(0)")
                code, _, end, protocol = runner.execute_child(child, 5)
                stamp = int(child.stdout.read())
                self.assertEqual((code, protocol), (0, "process"))
                self.assertIn(child.optimbench_exit_observation["method"],
                              ("pidfd-poll", "threaded-blocking-waitpid"))
                lags.append((end - stamp) / 1e6)
            # A shared VM may briefly deschedule the observer. Median avoids
            # testing scheduler luck; old polling adds ~25–45 ms at these sizes.
            self.assertLess(statistics.median(lags), 15, (delay, lags))

    def test_timeout_preserves_rule_d_kill_ownership(self):
        child = self.child("import time;time.sleep(10)")
        start = time.monotonic()
        code, _, _, protocol = runner.execute_child(child, .04)
        self.assertEqual((code, protocol), (None, "timeout"))
        self.assertIsNone(child.poll())  # Caller still performs freeze/kill.
        self.assertLess(time.monotonic() - start, 1)

    def test_signal_return_code_is_preserved(self):
        child = self.child("import os,signal;os.kill(os.getpid(), signal.SIGTERM)")
        code, _, _, protocol = runner.execute_child(child, 5)
        self.assertEqual((code, protocol), (-signal.SIGTERM, "process"))

    def test_zero_timeout_accepts_already_exited_unreaped_child(self):
        child = self.child("raise SystemExit(17)")
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        code, _, _, protocol = runner.execute_child(child, 0)
        self.assertEqual((code, protocol), (17, "process"))

    def test_unavailable_pidfd_uses_explicit_blocking_fallback(self):
        child = self.child("raise SystemExit(23)")
        with patch.object(os, "pidfd_open", side_effect=OSError(errno.ENOSYS, "not supported"), create=True):
            code, _, _, protocol = runner.execute_child(child, 5)
        self.assertEqual((code, protocol), (23, "process"))
        self.assertEqual(child.optimbench_exit_observation["method"], "threaded-blocking-waitpid")
        self.assertIn("not supported", child.optimbench_exit_observation["fallback_reason"])

    def test_fallback_timeout_also_leaves_kill_to_rule_d(self):
        child = self.child("import time;time.sleep(10)")
        with patch.object(os, "pidfd_open", side_effect=PermissionError("blocked"), create=True):
            code, _, _, protocol = runner.execute_child(child, .04)
        self.assertEqual((code, protocol), (None, "timeout"))
        self.assertIsNone(child.poll())
        self.assertEqual(child.optimbench_exit_observation["method"], "threaded-blocking-waitpid")
