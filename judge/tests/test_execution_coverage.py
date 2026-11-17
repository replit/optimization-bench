"""Execution evidence is source-bound, probe-bound and rejects unexecuted branches."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
import shutil
import subprocess

from judge import execution_coverage as coverage, runtime_state


class ExecutionCoverageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for real V8 coverage")
    def test_actual_v8_call_executes_but_an_uncalled_function_does_not(self):
        source = ("function scoped(){process.env.COVERAGE_FIXTURE='1';delete process.env.COVERAGE_FIXTURE;}\n"
                  "function unused(){process.env.NOT_CALLED='1';}\nscoped();\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            path = build / "api.js"
            path.write_text(source)
            directory = root / "v8-coverage"
            directory.mkdir()
            subprocess.run([shutil.which("node"), str(path)], check=True, capture_output=True,
                           env={**os.environ, "NODE_V8_COVERAGE": str(directory)})
            # Relocate only the fixture's public URL to the container build path.
            for profile in directory.glob("*.json"):
                value = json.loads(profile.read_text())
                for script in value["result"]:
                    if script["url"] == path.as_uri():
                        script["url"] = "file:///app/build/api.js"
                profile.write_text(json.dumps(value))
            evidence = coverage.node(root, build, "actual-node-run")
            state = {"candidate_probe_id": "actual-node-run", "execution_coverage": {"candidate": evidence}}
            rows = runtime_state.findings({}, {"api.js": source})
            self.assertEqual(len(rows), 3)
            for row in rows:
                self.assertEqual(coverage.candidate_executed(row, state, "node"), row["line"] == 1)

    def test_executed_restore_advisory_but_unused_and_worker_branches_block(self):
        source = """import gc
def scoped():
    old = gc.isenabled()
    gc.disable()
    try:
        pass
    finally:
        if old:
            gc.enable()
def worker():
    gc.freeze()
def unused():
    gc.disable()
if False: gc.disable()
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "candidate.py"
            path.write_text(source)
            collector = coverage.PythonCollector(root, "this-run")
            collector.start()
            try:
                spec = importlib.util.spec_from_file_location("candidate_coverage_fixture", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.scoped()
            finally:
                evidence = collector.stop()
            state = {"passed": True, "status": "passed", "differences": {},
                     "baseline": {"gc_enabled": True, "gc_frozen": False},
                     "candidate": {"gc_enabled": True, "gc_frozen": False},
                     "candidate_probe_id": "this-run", "execution_coverage": {"candidate": evidence}}
            rows = runtime_state.findings({}, {"candidate.py": source})
            result = runtime_state.assess(rows, state, "python")
            self.assertEqual(len(result["runtime_configuration_advisory"]), 2)
            blocked = result["runtime_configuration_review"]
            self.assertEqual(len(blocked), 3)
            self.assertTrue(all(row["review_reason"] == "not exercised by representative probe" for row in blocked))
            # Coverage for an import-time function definition or false branch
            # is not evidence that a call inside it executed.
            self.assertEqual({row["operation"] for row in blocked}, {"gc.freeze", "gc.disable"})
            executed = result["runtime_configuration_advisory"][0]
            state["candidate_probe_id"] = "different-run"
            self.assertFalse(coverage.candidate_executed(executed, state, "python"))
            state["candidate_probe_id"] = "this-run"
            self.assertFalse(coverage.candidate_executed({**executed, "source_sha256": "0" * 64}, state, "python"))
            self.assertFalse(coverage.candidate_executed({**executed, "path": "other/candidate.py"}, state, "python"))

    def test_v8_zero_count_nested_range_is_not_executed_by_positive_parent(self):
        source = "process.env.A='1';\nfunction unused(){process.env.B='2';}\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "v8-coverage").mkdir()
            build = root / "build"
            build.mkdir()
            (build / "api.js").write_text(source)
            start, end = source.index("function"), len(source)
            raw = {"result": [{"url": "file:///app/build/api.js", "functions": [
                {"ranges": [{"startOffset": 0, "endOffset": len(source), "count": 1}]},
                {"ranges": [{"startOffset": start, "endOffset": end, "count": 0}]}]}]}
            (root / "v8-coverage/test.json").write_text(json.dumps(raw))
            evidence = coverage.node(root, build, "node-run")
            rows = runtime_state.findings({}, {"api.js": source})
            state = {"candidate_probe_id": "node-run", "execution_coverage": {"candidate": evidence}}
            self.assertTrue(coverage.candidate_executed(rows[0], state, "node"))
            self.assertFalse(coverage.candidate_executed(rows[1], state, "node"))

    def test_go_blocks_require_positive_coverage_and_exact_source(self):
        source = 'package p\nfunc used(){debug.SetGCPercent(100)}\nfunc unused(){debug.SetGCPercent(1)}\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "build/project"
            build.mkdir(parents=True)
            (build / "api.go").write_text(source)
            (root / "go-cover.txt").write_text(
                "mode: count\nexample/p/api.go:2.1,2.35 1 1\nexample/p/api.go:3.1,3.35 1 0\n")
            evidence = coverage.go(root, root / "build", "example/p", "goldmark", "go-run")
            state = {"candidate_probe_id": "go-run", "execution_coverage": {"candidate": evidence}}
            rows = runtime_state.findings({}, {"api.go": source})
            self.assertTrue(coverage.candidate_executed(rows[0], state, "goldmark"))
            self.assertFalse(coverage.candidate_executed(rows[1], state, "goldmark"))

    def test_absent_native_evidence_never_discharges_a_clean_rounding_finding(self):
        rows = runtime_state.findings({}, {"x.c": "void f(){fesetround(0);}"})
        state = {"passed": True, "status": "passed", "differences": {},
                 "baseline": {"native_processes": [{"fp_rounding_mode": 0}]},
                 "candidate": {"native_processes": [{"fp_rounding_mode": 0}]}}
        result = runtime_state.assess(rows, state, "native")
        self.assertTrue(result["runtime_configuration_review"])
        self.assertFalse(result["runtime_configuration_advisory"])
