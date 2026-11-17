"""Static review is discharged only by a clean probe that observes the setting."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from judge import process_state, runtime_state
from judge import score_host
from judge.verifier import build_inventory, evaluate

ROOT = Path(__file__).resolve().parents[2]


def python_snapshot(code):
    run = subprocess.run([sys.executable, "-c", "import gc,json;from judge.process_probe import snapshot;"
                          + code + ";print(json.dumps(snapshot()))"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def clean(snapshot, rows=(), kind="python"):
    result = {"passed": True, "status": "passed", "differences": {},
              "baseline": snapshot, "candidate": snapshot}
    if rows:
        result["candidate_probe_id"] = "unit-probe"
        result["execution_coverage"] = {"candidate": {
            "kind": kind, "run_id": "unit-probe", "status": "collected",
            "files": [{"path": row["path"], "sha256": row["source_sha256"],
                       "executed_spans": [[row["start_byte"], row["end_byte"]]]}
                      for row in rows]}}
    return result


class RuntimeAdvisoryTests(unittest.TestCase):
    def test_advisory_is_recorded_without_short_circuiting_measurement(self):
        task = ROOT / "tasks/sqlglot-mozilla-transpile"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        build = Path(temporary.name)
        (build / "artifact").write_bytes(b"runtime-advisory fixture")
        build_sha = build_inventory(build)
        execution = {"effective_cores": list(range(8)), "requested_cores": list(range(8)),
                     "available_cores": list(range(16)), "pinning_took_effect": True,
                     "host": {"boot_id_sha256": "scorer"}}
        measured = {"correct": True, "aa_passed": True, "speedup": 1.1,
                    "cases": {}, "records": [], "status": "resolved"}
        rows = runtime_state.findings({}, {"x.py": "import gc\ngc.disable()\n"})
        with patch("judge.verifier.behavior.check", return_value={"process_state": clean({"gc_enabled": True}, rows)}), \
             patch("judge.verifier.interfaces.check", return_value={"passed": True}), \
             patch("judge.verifier.image_id", return_value="image"), \
             patch("judge.verifier.heldout.check", return_value={"status": "unavailable"}), \
             patch("judge.verifier.references", return_value=[{"path": "case", "expect_len": 0, "expect_sha": "hash"}]), \
             patch("judge.process_state.references", return_value=[{"path": "case", "expect_len": 0, "expect_sha": "hash"}]), \
             patch("judge.verifier.load_task", return_value=SimpleNamespace(cases=lambda _: [SimpleNamespace(id="case")])), \
             patch("judge.verifier.scheduler.session", return_value=measured) as session, \
             patch("judge.verifier.build_inventory", return_value=build_sha), \
             patch("judge.verifier.upstream.validate_receipt",
                    return_value={"reference_key": "reference", "reference_build_sha256": build_sha}), \
             patch("judge.reference.validate_call",
                    return_value={"key": "reference", "build_sha256": build_sha}), \
             patch("judge.verifier.telemetry.quality", return_value={"noisy": False}), \
             patch.object(score_host, "probe", return_value={"host": execution["host"], "counters": {}}):
            result = evaluate(task, build, build, build, "image", "0-7", "unregistered",
                              upstream_receipt=build / "receipt.json", source_sha256="source",
                              provisional=True, execution_host=execution, review_findings=rows)
        session.assert_called_once()
        self.assertEqual(result["speedup"], 1.1)
        self.assertFalse(result["runtime_configuration_review"])
        self.assertTrue(result["runtime_configuration_advisory"])

    def test_scoped_and_restored_settings_and_exit_hooks_are_advisory(self):
        cases = [
            ("x.py", "import gc\ngc.disable()\ntry: pass\nfinally: gc.enable()\n",
             "python", {"gc_enabled": True}),
            ("x.py", "import sys\nold=sys.getrecursionlimit()\nsys.setrecursionlimit(5000)\nsys.setrecursionlimit(old)\n",
             "python", {"recursion_limit": 1000}),
            ("x.go", 'package main\nimport "runtime/debug"\nfunc main(){debug.SetGCPercent(200)}',
             "goldmark", {"gc_percent": 100}),
            ("x.js", "process.on('exit',()=>{});", "node", {"active_handles": []}),
        ]
        for filename, source, kind, snapshot in cases:
            with self.subTest(filename=filename, source=source):
                rows = runtime_state.findings({}, {filename: source})
                self.assertTrue(rows)
                result = runtime_state.assess(rows, clean(snapshot, rows, kind), kind)
                self.assertFalse(result["runtime_configuration_review"])
                self.assertEqual(len(result["runtime_configuration_advisory"]), len(rows))

    def test_probe_missing_failed_not_configured_or_missing_fields_keeps_review(self):
        rows = runtime_state.findings({}, {"x.py": "import gc\ngc.disable()"})
        for report in [{}, {"passed": False, "status": "failed"}, {"passed": False, "status": "not_configured"},
                       {"passed": True, "status": "passed"}, clean({})]:
            with self.subTest(report=report):
                result = runtime_state.assess(rows, report, "python")
                self.assertTrue(result["runtime_configuration_review"])
                self.assertFalse(result["runtime_configuration_advisory"])

    def test_unobservable_overrides_stay_blocking_and_same_line_cannot_hide_them(self):
        cases = [
            ("x.rs", "#[global_allocator]\nstatic A: System = System;", "native"),
            ("x.cpp", "void* operator new(size_t n){return malloc(n);}", "native"),
            ("x.c", "void f(){fesetround(0);mallopt(1,2);setrlimit(1,0);sigaction(2,0,0);}", "native"),
            ("x.js", "v8.setFlagsFromString('--x');process.on('SIGINT',()=>{});", "node"),
            ("x.go", "package p\nfunc f(){debug.SetMemoryLimit(1);runtime.GOMAXPROCS(1)}", "goldmark"),
        ]
        for filename, source, kind in cases:
            with self.subTest(source=source):
                rows = runtime_state.findings({}, {filename: source})
                snapshot = {"native_processes": [{"fp_rounding_mode": 0, "environment": []}],
                            "exec_flags": [], "heap_limit": 123, "active_handles": [], "gc_percent": 100}
                result = runtime_state.assess(rows, clean(snapshot), kind)
                self.assertTrue(result["runtime_configuration_review"])
        rows = runtime_state.findings({}, {"x.c": "void f(){fesetround(0);mallopt(1,2);}"})
        self.assertEqual({row["category"] for row in rows}, {"fp_rounding", "allocator"})

    def test_aliases_and_python_signal_coverage_are_preserved(self):
        rows = runtime_state.findings({}, {"x.py": "from gc import disable as pause\npause()\n"
                                          "import signal as s\ns.signal(s.SIGINT,lambda *_:None)\n"})
        self.assertEqual({row["category"] for row in rows}, {"gc_enabled", "python_signal_handlers"})
        self.assertFalse(runtime_state.assess(rows, clean({"gc_enabled": True, "signal_handlers": {}}, rows),
                                              "python")["runtime_configuration_review"])
        # os.putenv updates C's environ, not Python's cached os.environ mapping.
        rows = runtime_state.findings({}, {"x.py": "import os\nos.putenv('X','1')"})
        self.assertTrue(runtime_state.assess(rows, clean({"environment": {}}),
                                             "python")["runtime_configuration_review"])

    def test_permanent_gc_freeze_is_blocked_by_actual_probe_difference(self):
        left, right = python_snapshot("pass"), python_snapshot("gc.freeze()")
        with patch.object(process_state, "probe", side_effect=[left, right]):
            report = process_state.check("task", "a", "b", "image", "0")
        self.assertFalse(report["passed"])
        self.assertIn("gc_frozen", report["differences"])
        rows = runtime_state.findings({}, {"x.py": "import gc\ngc.freeze()"})
        self.assertTrue(runtime_state.assess(rows, report, "python")["runtime_configuration_review"])

    def test_additional_gc_freeze_on_prefrozen_heap_is_blocked_by_actual_probe(self):
        left = python_snapshot("gc.freeze();tracked=[]")
        right = python_snapshot("gc.freeze();tracked=[];gc.freeze()")
        self.assertGreater(left["gc_frozen"], 0)
        self.assertGreater(right["gc_frozen"], left["gc_frozen"])
        with patch.object(process_state, "probe", side_effect=[left, right]):
            report = process_state.check("task", "a", "b", "image", "0")
        self.assertFalse(report["passed"])
        self.assertIn("gc_frozen", report["differences"])
        rows = runtime_state.findings({}, {"x.py": "import gc\ngc.freeze()"})
        self.assertTrue(runtime_state.assess(rows, report, "python")["runtime_configuration_review"])

    def test_unchanged_and_restored_gc_states_pass_actual_probe(self):
        for baseline, candidate in [
            ("pass", "pass"),
            ("gc.freeze();tracked=[]", "gc.freeze();tracked=[]"),
            ("gc.unfreeze()", "gc.unfreeze();gc.freeze();gc.unfreeze()"),
        ]:
            with self.subTest(baseline=baseline, candidate=candidate):
                left, right = python_snapshot(baseline), python_snapshot(candidate)
                self.assertIs(type(left["gc_frozen"]), int)
                self.assertIs(type(right["gc_frozen"]), int)
                with patch.object(process_state, "probe", side_effect=[left, right]):
                    report = process_state.check("task", "a", "b", "image", "0")
                self.assertTrue(report["passed"])
                self.assertEqual(report["differences"], {})

    @unittest.skipUnless(shutil.which("cc"), "C compiler required for actual fenv observation")
    def test_actual_restored_rounding_needs_coverage_and_a_leak_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state.jsonl"
            def snapshot(body, name):
                source, binary = root / (name + ".c"), root / name
                source.write_text("#include <fenv.h>\nint main(){" + body + "return 0;}")
                subprocess.run(["cc", str(source), str(ROOT / "judge/process_probe.c"),
                                "-lm", "-o", str(binary)], check=True, capture_output=True)
                state.unlink(missing_ok=True)
                subprocess.run([str(binary)], env={
                    "PATH": os.defpath, "LC_ALL": "C", "TZ": "UTC",
                    "OPTIMBENCH_STATE_FILE": str(state)}, check=True)
                rows = [json.loads(line) for line in state.read_text().splitlines()]
                identities = []
                for row in rows:
                    identity = row.pop("thread_identity", None)
                    if identity is not None:
                        identity["children_method"] = row.pop("children_method", None)
                        identities.append(identity)
                    row["environment"].sort()
                observed = process_state.ObservedState({"native_processes": rows}, {}, name)
                observed.native_exit_identities = identities
                return observed
            baseline = snapshot("", "baseline")
            restored = snapshot("int old=fegetround();fesetround(FE_DOWNWARD);fesetround(old);", "restored")
            leak = snapshot("fesetround(FE_DOWNWARD);", "leak")
            rows = runtime_state.findings({}, {"x.c": "void f(){fesetround(FE_DOWNWARD);}"})
            with patch.object(process_state, "probe", side_effect=[baseline, restored]):
                clean_result = process_state.check("task", "a", "b", "image", "0")
            self.assertTrue(clean_result["passed"])
            blocked = runtime_state.assess(rows, clean_result, "native")["runtime_configuration_review"]
            self.assertTrue(blocked)
            self.assertEqual(blocked[0]["review_reason"], "not exercised by representative probe")
            with patch.object(process_state, "probe", side_effect=[baseline, leak]):
                leak_result = process_state.check("task", "a", "b", "image", "0")
            self.assertFalse(leak_result["passed"])
            self.assertTrue(runtime_state.assess(rows, leak_result, "native")["runtime_configuration_review"])
