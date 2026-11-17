import errno
import copy
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


def compile_rust_test(source, binary):
    """Use local Rust or the pinned task compiler, not an unpinned installation."""
    command = ["rustc", "--edition=2021", "--test", str(source), "-o", str(binary)]
    if shutil.which("rustc"):
        return subprocess.run(command, capture_output=True, check=True, timeout=120)
    image = os.environ.get("RUST_TEST_IMAGE")
    if not image:
        raise RuntimeError("Rust fixture requires rustc or explicit pinned RUST_TEST_IMAGE")
    command = ["docker", "run", "--rm", "--network=none", "--cap-drop=ALL",
               "--security-opt=no-new-privileges",
               "-v", str(source.parent.resolve()) + ":/fixture:ro",
               "-v", str(binary.parent.resolve()) + ":/output",
               image, "rustc", "--edition=2021", "--test",
               "/fixture/" + source.name, "-o", "/output/" + binary.name]
    return subprocess.run(command, capture_output=True, check=True, timeout=120)

from tools.fuzz_api_faults.factories import InvalidCase, block_case, match_case, sequence_request, SQLFluffBlocks, SQLFluffMatches
from tools.fuzz_api_faults.lifecycle import ChildListUnsupported, _live_threads, observe, snapshot, validate_snapshot
from tools.fuzz_api_faults.streams import FaultPlan, FaultReader, FaultWriter
from tools.fuzz_api_faults.worker import execute, execute_request, semantic

ROOT = Path(__file__).resolve().parents[2]


class StreamTests(unittest.TestCase):
    def test_partial_write_then_error_accepts_zero_on_error(self):
        w = FaultWriter(FaultPlan(fail_after=2, error_kind="BrokenPipe"))
        self.assertEqual(w.write(b"abcdef"), 2)
        with self.assertRaises(BrokenPipeError):
            w.write(b"cdef")
        self.assertEqual(w.accepted_bytes, b"ab")
        self.assertEqual(w.schedule.error_count, 1)

    def test_short_call_partition_does_not_change_logical_fault_boundary(self):
        for chunk in (1, 3, 32):
            w = FaultWriter(FaultPlan(max_chunk=chunk, fail_after=5))
            data = b"abcdefghi"
            offset = 0
            with self.assertRaises(OSError):
                while offset < len(data):
                    offset += w.write(data[offset:])
            self.assertEqual(w.accepted_bytes, b"abcde")

    def test_one_shot_eintr_splits_crossing_request(self):
        w = FaultWriter(FaultPlan(interrupt_at=2))
        self.assertEqual(w.write(b"abcde"), 2)
        with self.assertRaises(InterruptedError):
            w.write(b"cde")
        self.assertEqual(w.write(b"cde"), 3)
        self.assertEqual(w.accepted_bytes, b"abcde")
        self.assertEqual(w.schedule.error_count, 1)

    def test_reader_prefix_then_error_and_eof_are_distinct(self):
        reader = FaultReader(b"abcdef", FaultPlan(fail_after=2))
        self.assertEqual(reader.read(10), b"ab")
        with self.assertRaises(OSError):
            reader.read(10)
        reader = FaultReader(b"abcdef", FaultPlan(eof_after=2))
        self.assertEqual(reader.read(10), b"ab")
        self.assertEqual(reader.read(10), b"")

    def test_read_interruption_and_empty_buffers(self):
        r = FaultReader(b"abc", FaultPlan(interrupt_at=1))
        self.assertEqual(r.read(3), b"a")
        self.assertEqual(r.read(0), b"")
        with self.assertRaises(InterruptedError):
            r.read(3)
        self.assertEqual(r.read(3), b"bc")
        self.assertEqual(r.schedule.error_count, 1)

    def test_no_implied_flush(self):
        w = FaultWriter(FaultPlan(flush_error=True))
        self.assertEqual(w.write(b"data"), 4)
        self.assertEqual(w.schedule.error_count, 0)
        with self.assertRaises(OSError):
            w.flush()
        # Avoid another deliberate flush error from RawIOBase.__del__.
        object.__setattr__(w.schedule, "plan", FaultPlan())
        w.close()

    def test_illegal_fault_plans_rejected(self):
        for plan in (FaultPlan(max_chunk=0), FaultPlan(max_chunk=True),
                     FaultPlan(fail_after=1, zero_after=1), FaultPlan(error_kind="Interrupted")):
            with self.assertRaises(ValueError):
                FaultWriter(plan)
        with self.assertRaises(ValueError):
            FaultReader(b"", FaultPlan(flush_error=True))

    def test_zero_write_does_not_mean_bytes_were_accepted(self):
        w = FaultWriter(FaultPlan(zero_after=2))
        self.assertEqual(w.write(b"abcd"), 2)
        self.assertEqual(w.write(b"cd"), 0)
        self.assertEqual(w.accepted_bytes, b"ab")

    def test_rust_shared_wrappers_compile_and_run(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "fault-tests"
            compile_rust_test(ROOT / "tools/fuzz_api_faults/faults.rs", binary)
            result = subprocess.run([str(binary)], capture_output=True, check=True, timeout=60)
            self.assertIn(b"10 passed", result.stdout)

    def test_native_resource_snapshot_compiles_and_runs(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "proc-tests"
            compile_rust_test(ROOT / "tools/fuzz_api_faults/proc.rs", binary)
            # These tests observe process-wide live resources. Other libtest
            # workers starting/exiting are unrelated churn, not the subject
            # of the per-call snapshots. Isolate tests, not production facts.
            result = subprocess.run([str(binary), "--test-threads=1"],
                                    capture_output=True, check=True, timeout=60)
            self.assertIn(b"9 passed", result.stdout)


class StateAndLifecycleTests(unittest.TestCase):
    def test_live_threads_bind_kernel_identity_count_clock_and_phase(self):
        facts = snapshot("before")
        self.assertTrue(validate_snapshot(facts)["complete"])
        identities = facts["thread_identities"]
        self.assertEqual(facts["threads"], len(identities))
        self.assertEqual(facts["counts"]["threads"], len(identities))
        self.assertEqual(len({r["tid"] for r in identities}), len(identities))
        self.assertEqual(facts["phase"], "before")
        self.assertLessEqual(facts["sample_clock"]["start_ns"], facts["sample_clock"]["end_ns"])
        for row in identities:
            self.assertGreater(row["tid"], 0)
            self.assertGreaterEqual(row["start_ticks"], 0)
            self.assertNotIn(row["state"], {"Z", "X", "x"})
            self.assertEqual(row["scope_pid"], facts["proc_pid"])
        for fd in facts["fds"]:
            self.assertEqual(fd["target"], "sha256:" + fd["target_sha256"])
            self.assertEqual(len(fd["target_sha256"]), 64)

    def test_thread_count_mismatch_invalid_but_absent_identity_pending(self):
        facts = snapshot("after")
        bad = copy.deepcopy(facts)
        bad["threads"] += 1
        with self.assertRaises(ValueError):
            validate_snapshot(bad)
        bad = copy.deepcopy(facts)
        bad["counts"]["threads"] = True
        with self.assertRaises(ValueError):
            validate_snapshot(bad)
        facts["thread_identities"] = None
        self.assertFalse(validate_snapshot(facts)["complete"])

    def test_dead_and_vanished_threads_are_filtered_by_state_not_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = root / "task"
            task.mkdir()
            for tid, state in ((1, "R"), (2, "Z"), (3, "X"), (4, "x")):
                path = task / str(tid)
                path.mkdir()
                (path / "stat").write_text(
                    f"{tid} (any misleading worker name) {state} 100 " + "0 " * 17 + f"{10 + tid} 0\n")
                (path / "status").write_text("Tgid:\t100\n")
            original = Path.iterdir
            def enumeration(path):
                values = list(original(path))
                return iter(values + [task / "99"]) if path == task else iter(values)
            with patch.object(Path, "iterdir", enumeration):
                rows = _live_threads(root, 100)
            self.assertEqual([r["tid"] for r in rows], [1])
            # Missing stat on a still-visible task is unavailable, not vanished.
            (task / "98").mkdir()
            with self.assertRaises(OSError):
                _live_threads(root, 100)

    def test_child_ppid_fallback_does_not_turn_unsupported_into_zero(self):
        child = subprocess.Popen(["sleep", "10"])
        try:
            with patch("tools.fuzz_api_faults.lifecycle._task_children", side_effect=ChildListUnsupported()):
                facts = snapshot()
            self.assertTrue(facts["availability"]["children"])
            self.assertEqual(facts["children_method"], "proc-stat-ppid-fallback")
            self.assertIn(child.pid, {row["pid"] for row in facts["children"]})
        finally:
            child.terminate()
            child.wait()

    def test_failed_child_measurement_preserves_fd_thread_availability(self):
        with patch("tools.fuzz_api_faults.lifecycle._task_children", side_effect=ChildListUnsupported()):
            with patch("tools.fuzz_api_faults.lifecycle.children_by_ppid", side_effect=PermissionError()):
                facts = snapshot()
        self.assertFalse(facts["availability"]["children"])
        self.assertTrue(facts["availability"]["fds"])
        self.assertTrue(facts["availability"]["threads"])
        self.assertIsNone(facts["counts"]["children"])
        self.assertIsNone(facts["children"])

    def test_target_stdout_and_stderr_do_not_corrupt_private_report_channel(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = Path(d) / "runtime"
            package = runtime / "sqlfluff/utils/reflow"
            package.mkdir(parents=True)
            for folder in (runtime / "sqlfluff", runtime / "sqlfluff/utils", package):
                (folder / "__init__.py").write_text("")
            (package / "config.py").write_text(
                "from dataclasses import dataclass\nimport sys,os\n"
                "@dataclass\nclass Block:\n"
                " spacing_before:str='single'\n spacing_after:str='single'\n"
                " spacing_within:object=None\n line_position:object=None\n"
                " keyword_line_position:object=None\n keyword_line_position_exclusions:object=None\n"
                " def incorporate(self, **kw):\n"
                "  for k,v in kw.items(): setattr(self,'spacing_'+k,v)\n"
                "class ReflowConfig:\n"
                " @classmethod\n def from_dict(cls,cfg):\n"
                "  obj=cls();obj.cfg=cfg;return obj\n"
                " def get_block_config(self,types):\n"
                "  print('target stdout',flush=True);print('target stderr',file=sys.stderr,flush=True)\n"
                "  os.write(1,b'native stdout\\n');os.write(2,b'native stderr\\n')\n"
                "  block=Block()\n"
                "  for typ in types:\n"
                "   for k,v in self.cfg.get(typ,{}).items():setattr(block,k,v)\n"
                "  return block\n"
            )
            request = sequence_request()
            result = subprocess.run(
                [sys.executable, "-m", "tools.fuzz_api_faults.worker", "--runtime", str(runtime)],
                input=json.dumps(request).encode() + b"\n", capture_output=True, timeout=60, check=True,
                cwd=ROOT, env={"PATH": os.defpath, "HOME": d, "PYTHONPATH": str(ROOT),
                               "PYTHONDONTWRITEBYTECODE": "1"},
            )
            response = json.loads(result.stdout)
            self.assertEqual(response["status"], "observed")
            getters = [step for seq in response["sequence_results"] for step in seq["observations"]
                       if step["operation"] == "get"]
            self.assertTrue(getters)
            for step in getters:
                self.assertEqual(bytes.fromhex(step["effects"]["target_stdout_hex"]), b"target stdout\nnative stdout\n")
                self.assertEqual(bytes.fromhex(step["effects"]["target_stderr_hex"]), b"target stderr\nnative stderr\n")
            self.assertNotIn(b"target stderr", result.stderr)

    def test_preferred_protocol_rejects_all_bad_cases_before_import(self):
        request = sequence_request()
        request["cases"][1]["operations"].append({"op": "private_mutation", "handle": "first"})
        with patch.object(SQLFluffBlocks, "__init__", side_effect=AssertionError("must not import")):
            with self.assertRaises(InvalidCase):
                execute_request(request)

    def test_preferred_protocol_has_aba_and_bounded_repetitions(self):
        request = sequence_request()
        self.assertEqual(request["order"], ["A", "B", "A"])
        self.assertEqual(request["repetitions"], 3)
        request["repetitions"] = True
        with self.assertRaises(InvalidCase):
            execute_request(request)

    def test_invalid_mutation_rejected_before_candidate_import(self):
        case = match_case()
        case["sequence"].append({"op": "mutate_segment_kwargs", "handle": "first", "key": "x"})
        with patch.object(SQLFluffMatches, "__init__", side_effect=AssertionError("must not import")):
            with self.assertRaises(InvalidCase):
                execute(case)

    def test_block_precondition_accepts_only_documented_mutator(self):
        case = block_case()
        SQLFluffBlocks.precondition(case["sequence"])
        case["sequence"][4]["kwargs"] = {"private_cache": "touch"}
        with self.assertRaises(InvalidCase):
            SQLFluffBlocks.precondition(case["sequence"])

    def test_resources_recorded_around_each_call(self):
        outcome, resources = observe(lambda: {"ok": True})
        self.assertEqual(outcome["value"], {"ok": True})
        self.assertTrue(resources["before"]["available"])
        self.assertIn("fds", resources["after"])
        self.assertIn("threads", resources["after"])
        self.assertIn("children", resources["after"])

    def test_joined_transient_worker_is_not_a_survivor(self):
        def call():
            thread = threading.Thread(target=lambda: None)
            thread.start()
            thread.join()
            # Python 3.12 join() can return before the native task exits.
            native_task = Path(f"/proc/self/task/{thread.native_id}")
            deadline = time.monotonic() + 5
            while True:
                try:
                    native_task.stat()
                except FileNotFoundError:
                    break
                self.assertLess(time.monotonic(), deadline,
                                f"joined native thread {thread.native_id} did not exit")
            return thread.native_id
        outcome, resources = observe(call)
        self.assertEqual(outcome["kind"], "ok", outcome)
        for phase in ("before", "after"):
            self.assertTrue(validate_snapshot(resources[phase])["complete"], resources[phase])
            self.assertEqual(resources[phase]["phase"], phase)
        self.assertNotIn(outcome["value"], {row["tid"] for row in resources["after"]["thread_identities"]})
        self.assertEqual(resources["before"]["threads"], resources["after"]["threads"], resources)

    def test_live_descriptor_is_visible_then_cleanup(self):
        retained = []
        def call():
            retained.append(open(os.devnull, "rb"))
        try:
            _, resources = observe(call)
            self.assertGreater(resources["after"]["counts"]["fds"], resources["before"]["counts"]["fds"])
        finally:
            for handle in retained:
                handle.close()

    def test_library_error_is_typed_not_driver_failure(self):
        outcome, _ = observe(lambda: (_ for _ in ()).throw(BrokenPipeError(errno.EPIPE, "failure")))
        self.assertEqual(outcome["kind"], "error")
        self.assertEqual(outcome["class"], "builtins.BrokenPipeError")


class ReferenceEvidenceTests(unittest.TestCase):
    def test_source_bound_reference_detects_aliasing_but_not_frozen_interning(self):
        base = ROOT / "tools/fuzz_api_faults"
        evidence = base / "evidence/thread-identities-v2"
        summary = json.loads((evidence / "summary.json").read_text())
        raw = gzip.decompress((evidence / "validation.json.gz").read_bytes())
        report = json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), summary["validation_sha256"])
        for name, field in (("worker.py", "worker_source_sha256"),
                            ("factories.py", "factory_source_sha256")):
            self.assertEqual(hashlib.sha256((base / name).read_bytes()).hexdigest(), summary[field])
        # Immutable historical captures predate PF_EXITING collection. Bind
        # their original sampler identity, never relabel them as new evidence.
        self.assertEqual(summary["lifecycle_source_sha256"],
                         "8149c05333e8654c5b1fdc2a0d38d078449eaa920be037dd07b112e7aa585912")
        self.assertEqual(summary["lifecycle_source_sha256"], report["lifecycle_source_sha256"])
        self.assertTrue(report["shared_mutable_detected"])
        self.assertTrue(report["frozen_interning_must_not_flag"])
        self.assertFalse(report["qualification_claimed"])
        baseline = report["arms"]["pristine"]["observations"]
        frozen = report["arms"]["frozen-interned"]["observations"]
        unsafe = report["arms"]["shared-mutable"]["observations"]
        stable = lambda value: json.dumps(value, sort_keys=True)
        self.assertEqual(stable(baseline), stable(frozen))
        self.assertNotEqual(stable(baseline), stable(unsafe))
        self.assertEqual(summary["public_call_records"], 1026)

    def test_retained_raw_per_call_resource_facts_match_hashes(self):
        base = ROOT / "tools/fuzz_api_faults/evidence/thread-identities-v2"
        manifest = json.loads((base / "artifacts.json").read_text())
        self.assertEqual(len(manifest["files"]), 6)
        for row in manifest["files"]:
            compressed = (base / row["path"]).read_bytes()
            raw = gzip.decompress(compressed)
            self.assertEqual(hashlib.sha256(compressed).hexdigest(), row["stored_sha256"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["raw_sha256"])
            self.assertEqual(len(raw), row["raw_bytes"])
            requests = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(requests), 2)
            for request in requests:
                self.assertEqual(len(request["sequence_results"]), 9)
                for sequence in request["sequence_results"]:
                    for step in sequence["observations"]:
                        self.assertIn("target_stdout_hex", step["effects"])
                        for side in ("before", "after"):
                            facts = step["diagnostics"]["lifecycle"][side]
                            self.assertTrue(facts["available"])
                            self.assertTrue(facts["availability"]["children"])
                            self.assertIn("proc_pid", facts)
                            # Flags were not recorded in this historical
                            # receipt, so no retrospective PF_EXITING ruling.
                            self.assertEqual(validate_snapshot(facts), {
                                "complete": False, "reason": "thread_flags_unavailable"})
                            self.assertEqual(facts["phase"], side)
                            self.assertEqual(facts["threads"], len(facts["thread_identities"]))


if __name__ == "__main__":
    unittest.main()
