"""Identity-safe observer shutdown settling, with real Linux orphan controls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from judge.fuzzing_processes import classify_pairs
from tools.fuzz_runners import cli_faults


class ShutdownObservationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"),
                         "Set a local native Docker image for full CLI observer control")
    def test_native_late_stdout_and_stderr_are_not_hidden_by_root_exit_snapshot(self):
        import json
        from judge.fuzzing_io import cli_semantic
        from tools.runner.runner import Step

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "input.sql").write_text("SELECT 1;\n")
            builds = {arm: root / arm for arm in ("baseline", "candidate")}
            for build in builds.values():
                build.mkdir()
            (builds["baseline"] / "tool.py").write_text("print('correct',flush=True)\n")
            for stream in ("stdout", "stderr"):
                with self.subTest(stream=stream):
                    (builds["candidate"] / "tool.py").write_text(
                        "import os,sys,time\nprint('correct',flush=True)\n"
                        "if os.fork() != 0:\n os._exit(0)\n"
                        f"time.sleep(.1)\nprint('late-extra',file=sys.{stream},flush=True)\n")
                    result = cli_faults.run_pair(
                        step=Step(["python3", "{build}/tool.py"], timeout=5),
                        inputs=inputs, baseline=builds["baseline"], candidate=builds["candidate"],
                        image=os.environ["OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"],
                        out=root / stream, watch_inputs=["input.sql"],
                        faults=["healthy"], cpuset="0")
                    pair = result["observations"]["healthy"]
                    self.assertEqual(pair["baseline"]["status"], "observed")
                    published = pair["candidate"]
                    self.assertEqual(published["status"], "invalid_run")
                    # Publication independently rejects a final stream that
                    # differs from its root-exit commitment, replacing the
                    # detailed row with a fail-closed collector error.
                    row = json.loads((root / stream / "healthy" / "candidate" /
                                      "observer.json").read_text())
                    self.assertEqual(row["status"], "invalid_run")
                    self.assertIn(stream, row["post_exit_stream_mutations"])
                    self.assertEqual(row["stdout"]["sha256"], pair["baseline"]["stdout"]["sha256"])
                    self.assertNotEqual(row[stream]["sha256"],
                                        row["post_settle_stream_effects"][stream]["sha256"])
                    self.assertFalse(row["stopped_descendants"])
                    with self.assertRaisesRegex(ValueError, "raw stream is not the committed regular file"):
                        cli_semantic(published, root / stream)

    @unittest.skipUnless(os.environ.get("OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"),
                         "Set a local native Docker image for full CLI observer control")
    def test_native_delayed_child_output_cannot_become_accepted_during_barrier(self):
        from judge.fuzzing_io import cli_semantic
        from tools.runner.runner import Step

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "input.sql").write_text("SELECT 1;\n")
            builds = {arm: root / arm for arm in ("baseline", "candidate")}
            for arm, build in builds.items():
                build.mkdir()
                code = "from pathlib import Path\n"
                if arm == "candidate":
                    code += (
                        "import os,time\n"
                        "if os.fork() != 0:\n"
                        " os._exit(0)\n"
                        "time.sleep(.15)\n"
                    )
                code += "Path('result.sql').write_text(Path('input.sql').read_text())\n"
                (build / "tool.py").write_text(code)
            result = cli_faults.run_pair(
                step=Step(["python3", "{build}/tool.py"], timeout=5),
                inputs=inputs, baseline=builds["baseline"], candidate=builds["candidate"],
                image=os.environ["OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"],
                out=root / "run", watch_inputs=["input.sql"], watch_outputs=["result.sql"],
                required_outputs=["result.sql"], faults=["healthy"], cpuset="0")
            pair = result["observations"]["healthy"]
            self.assertEqual(pair["baseline"]["status"], "observed")
            cli_semantic(pair["baseline"], root / "run")
            candidate = pair["candidate"]
            self.assertEqual(candidate["status"], "invalid_run")
            self.assertEqual(candidate["required_outputs"]["result.sql"]["result.sql"],
                             {"kind": "missing"})
            late = candidate["post_settle_file_effects"]["required_outputs"]
            self.assertEqual(late["result.sql"]["result.sql"]["kind"], "file")
            self.assertIn("required_outputs", candidate["post_exit_file_mutations"])
            self.assertFalse(candidate["stopped_descendants"])
            with self.assertRaisesRegex(ValueError, "post-exit shutdown barrier"):
                cli_semantic(candidate, root / "run")

    @unittest.skipUnless(os.environ.get("OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"),
                         "Set a local native Docker image for full CLI observer control")
    def test_native_persistent_orphan_still_blocks_compatibility(self):
        from tools.runner.runner import Step

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "input.sql").write_text("SELECT 1;\n")
            builds = {arm: root / arm for arm in ("baseline", "candidate")}
            for arm, build in builds.items():
                build.mkdir()
                code = ""
                if arm == "candidate":
                    code = (
                        "import os,time\n"
                        "if os.fork() == 0:\n"
                        " os.setsid()\n"
                        " os.close(1)\n"
                        " os.close(2)\n"
                        " time.sleep(30)\n"
                        " os._exit(0)\n"
                    )
                (build / "tool.py").write_text(
                    code + "from pathlib import Path\nprint(Path('input.sql').read_text(),end='')\n")
            pairs = []
            for repeat in range(2):
                result = cli_faults.run_pair(
                    step=Step(["python3", "{build}/tool.py"], timeout=5),
                    inputs=inputs, baseline=builds["baseline"], candidate=builds["candidate"],
                    image=os.environ["OPTIMBENCH_NATIVE_CLI_SHUTDOWN_IMAGE"],
                    out=root / f"run-{repeat}", watch_inputs=["input.sql"],
                    faults=["healthy"], cpuset="0")
                pair = result["observations"]["healthy"]
                pairs.append(pair)
                for arm in builds:
                    self.assertEqual(pair[arm]["status"], "observed")
                    self.assertTrue(pair[arm]["raw_streams_retained"])
                self.assertFalse(pair["baseline"]["stopped_descendants"])
                self.assertTrue(pair["candidate"]["stopped_descendants"])
                settled = pair["candidate"]["post_exit_settlement"]
                self.assertTrue(settled["deadline_reached"])
                self.assertTrue(settled["initial"]["processes"])
                self.assertTrue(settled["final"]["processes"])
                self.assertTrue(all("start_ticks" in row for row in settled["final"]["processes"]))
            self.assertEqual(classify_pairs(pairs)["status"], "needs_review")

    def test_recycled_pid_is_not_reported_or_reaped_as_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / "stat").write_text("")
            first = "7 (python) S " + " ".join(["1"] * 18 + ["40"] + ["0"] * 5)
            second = first.replace("40", "41")
            with patch.object(Path, "read_text", side_effect=[first, second]), \
                    patch.object(Path, "read_bytes", return_value=b"python\0"):
                self.assertIsNone(cli_faults._process_identity(7))
        row = {"pid": 7, "start_ticks": 40, "state": "Z"}
        with patch.object(cli_faults, "_descendants", side_effect=[[row], []]), \
                patch.object(cli_faults, "_process_identity",
                             return_value={**row, "start_ticks": 41}), \
                patch.object(cli_faults.os, "waitpid") as reap:
            result = cli_faults._settle_descendants(6)
        reap.assert_not_called()
        self.assertEqual(result["initial"]["processes"], [row])
        self.assertEqual(result["final"]["processes"], [])

    def test_persistent_process_has_both_time_and_snapshot_bounds(self):
        row = {"pid": 7, "start_ticks": 40, "state": "S", "cmdline": "user worker"}
        with patch.object(cli_faults, "_descendants", return_value=[row]), \
                patch.object(cli_faults.time, "monotonic", return_value=0), \
                patch.object(cli_faults.time, "sleep"):
            result = cli_faults._settle_descendants(6)
        self.assertTrue(result["deadline_reached"])
        self.assertEqual(len(result["transitions"]) + 2, cli_faults.POST_EXIT_MAX_SNAPSHOTS)
        pair = {"baseline": {"stopped_descendants": []},
                "candidate": {"stopped_descendants": result["final"]["processes"]}}
        self.assertEqual(classify_pairs([pair, pair])["status"], "needs_review")

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper/procfs control")
    def test_real_transient_and_persistent_orphans_keep_full_evidence(self):
        # Isolate subreaper adoption from the test runner's other subprocesses.
        program = r'''
import json, os, signal, subprocess, sys
from tools.fuzz_runners.cli_faults import _subreaper, _settle_descendants, _process_identity
_subreaper()
root = subprocess.Popen([sys.executable, "-c", """
import os,time
if os.fork() == 0:
    time.sleep(float(__import__('sys').argv[1]))
    os._exit(0)
os._exit(0)
""", sys.argv[1]], start_new_session=True)
root.wait()
result = _settle_descendants(root.pid)
for row in result["final"]["processes"]:
    live = _process_identity(row["pid"])
    if live is not None and live["start_ticks"] == row["start_ticks"]:
        os.kill(row["pid"], signal.SIGKILL)
        os.waitpid(row["pid"], 0)
print(json.dumps(result))
'''
        root = Path(__file__).resolve().parents[2]
        results = []
        for lifetime in (".08", "30"):
            run = subprocess.run([sys.executable, "-c", program, lifetime],
                                 cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                                 capture_output=True, text=True, timeout=5, check=True)
            results.append(json.loads(run.stdout))
        transient, persistent = results
        self.assertTrue(transient["initial"]["processes"])
        self.assertTrue(transient["transitions"])
        self.assertTrue(transient["reaped"])
        self.assertFalse(transient["final"]["processes"])
        self.assertFalse(transient["deadline_reached"])
        self.assertTrue(persistent["final"]["processes"])
        self.assertTrue(persistent["deadline_reached"])
        self.assertGreaterEqual(persistent["final"]["elapsed_seconds_diagnostic_only"],
                                cli_faults.POST_EXIT_SETTLE_SECONDS)
        pair = {"baseline": {"stopped_descendants": []},
                "candidate": {"stopped_descendants": persistent["final"]["processes"]}}
        self.assertEqual(classify_pairs([pair, pair])["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
