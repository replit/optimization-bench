import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.fuzz_runners.coverage import CoverageError, normalize
from tools.fuzz_runners.plans import build_plan, engine_argv
from tools.fuzz_runners.receipt import artifact, build_receipt, coverage_receipt, image


class CoverageTests(unittest.TestCase):
    def test_actual_gcc15_counter_projection_keeps_zero_branches(self):
        fixture = Path(__file__).resolve().parents[1] / "fuzz_runners/fixtures/gcov-gcc15.json"
        result = normalize("gcov", json.loads(fixture.read_text()), "/capability-fixtures")
        self.assertEqual(result["status"], "available")
        record = result["files"]["gcov-control.c"]
        self.assertEqual(len(record["branches"]), 6)
        self.assertEqual(sum(count == 0 for count in record["branches"].values()), 2)
        self.assertEqual(record["lines"]["8"], 0)
        self.assertIn(16, record["partial_lines"])

    def test_gcov_without_branch_export_never_claims_zero_denominator(self):
        data = {"format_version": "2", "files": [{"file": "source.c", "lines": [
            {"line_number": 1, "count": 1}]}]}
        self.assertEqual(normalize("gcov", data, "/source")["status"], "unsupported")

    def test_gcov_negative_counters_rejected(self):
        data = {"format_version": "2", "files": [{"file": "source.c", "lines": [
            {"line_number": 1, "count": -1, "branches": []}]}]}
        with self.assertRaises(CoverageError):
            normalize("gcov", data, "/source")

    def llvm(self):
        return {"type": "llvm.coverage.json.export", "data": [{"files": [{
            "filename": "/source/unit.c",
            "segments": [[1, 1, 2, True, True, False], [2, 1, 0, False, False, False]],
            "branches": [[1, 2, 1, 8, 2, 0]],
        }]}]}

    def test_llvm_line_and_uncovered_branch(self):
        result = normalize("llvm", self.llvm(), "/source")
        self.assertEqual(result["files"]["unit.c"], {
            "lines": {"1": 2}, "branches": {"1:2:1:8:true": 2, "1:2:1:8:false": 0}})
        self.assertEqual(result["status"], "available")

    def test_gap_region_not_executable(self):
        data = self.llvm()
        data["data"][0]["files"][0]["segments"][0][5] = True
        self.assertEqual(normalize("llvm", data, "/source")["files"]["unit.c"]["lines"], {})

    def test_llvm_missing_branch_map_is_unsupported(self):
        data = self.llvm()
        del data["data"][0]["files"][0]["branches"]
        self.assertEqual(normalize("llvm", data, "/source")["status"], "unsupported")

    def test_negative_counter_rejected(self):
        data = self.llvm()
        data["data"][0]["files"][0]["branches"][0][4] = -1
        with self.assertRaises(CoverageError):
            normalize("llvm", data, "/source")

    def test_non_task_libraries_not_credited(self):
        data = self.llvm()
        data["data"][0]["files"][0]["filename"] = "/usr/include/test.h"
        result = normalize("llvm", data, "/source")
        self.assertEqual(result["files"], {})
        self.assertEqual(result["excluded_files"], ["/usr/include/test.h"])
        self.assertEqual(result["status"], "unsupported")

    def test_unexported_macro_is_limitation(self):
        data = self.llvm()
        data["data"][0]["files"][0]["expansions"] = [{"filenames": ["/source/macro.h"]}]
        self.assertEqual(normalize("llvm", data, "/source")["status"], "unsupported")

    def test_python_exit_branch_and_missing_arc(self):
        data = {"meta": {"branch_coverage": True}, "files": {"unit.py": {
            "executed_lines": [1, 2], "missing_lines": [3],
            "executed_branches": [[1, 2], [2, -1]], "missing_branches": [[1, 3]]}}}
        result = normalize("python", data, "/source")["files"]["unit.py"]
        self.assertEqual(result["lines"], {"1": 1, "2": 1, "3": 0})
        self.assertEqual(result["branches"], {"1:2": 1, "2:-1": 1, "1:3": 0})

    def test_python_line_only_rejected(self):
        with self.assertRaises(CoverageError):
            normalize("python", {"meta": {"branch_coverage": False}}, "/source")

    def test_python_truncated_per_file_branch_export_unsupported(self):
        data = {"meta": {"branch_coverage": True},
                "files": {"unit.py": {"executed_lines": [1], "missing_lines": []}}}
        self.assertEqual(normalize("python", data, "/source")["status"], "unsupported")

    def test_istanbul_uses_statement_start_not_text_span(self):
        data = {"unit.js": {"statementMap": {"0": {
            "start": {"line": 1, "column": 0}, "end": {"line": 4, "column": 1}}},
            "s": {"0": 1}, "branchMap": {"0": {"type": "if", "locations": [
                {"start": {"line": 2, "column": 2}, "end": {"line": 2, "column": 5}},
                {"start": {"line": 3, "column": 2}, "end": {"line": 3, "column": 5}}]}},
            "b": {"0": [1, 0]}}}
        result = normalize("istanbul", data, "/source")["files"]["unit.js"]
        self.assertEqual(result["lines"], {"1": 1})
        self.assertEqual(list(result["branches"].values()), [1, 0])
        data["unit.js"]["inputSourceMap"] = {"version": 3}
        with self.assertRaises(CoverageError):
            normalize("istanbul", data, "/source")

    def test_go_blocks_do_not_manufacture_lines_or_branches(self):
        data = "mode: atomic\nexample.com/pkg/unit.go:1.1,8.2 3 7\n"
        result = normalize("go", data, "/source", path_mappings={"example.com/pkg": "/source"})
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["files"]["unit.go"]["lines"], {})
        self.assertEqual(result["files"]["unit.go"]["branches"], {})
        self.assertEqual(len(result["limitations"]), 2)

    def test_explicit_go_inventories_preserve_zero_outcomes(self):
        branches = {"schema": "optimbench-go-branches-v1", "branches": [
            {"file": "unit.go", "line": 2, "id": "if2-false", "count": 0},
            {"file": "unit.go", "line": 2, "id": "if2-true", "count": 7}]}
        result = normalize("go", "mode: atomic\nunit.go:1.1,8.2 3 7\n", "/source",
                           executable_lines={"unit.go": [2, 5]}, go_branches=branches)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["files"]["unit.go"]["lines"], {"2": 7, "5": 7})
        self.assertEqual(result["files"]["unit.go"]["branches"]["2:if2-false"], 0)
        branches["branches"].append(branches["branches"][0])
        with self.assertRaises(CoverageError):
            normalize("go", "mode: atomic\nunit.go:1.1,8.2 3 7\n", "/source",
                      executable_lines={"unit.go": [2]}, go_branches=branches)

    def test_go_module_path_mapping_applies_to_line_inventory(self):
        data = normalize("go", "mode: atomic\nexample.org/pkg/unit.go:1.1,8.2 3 7\n", "/source",
                         executable_lines={"unit.go": [2]}, go_branches={"schema": "optimbench-go-branches-v1", "branches": []},
                         path_mappings={"example.org/pkg": "/source"})
        self.assertEqual(data["files"]["unit.go"]["lines"], {"2": 7})

    def test_unresolved_webpack_source_is_not_task_path(self):
        data = {"webpack://task/a.js": {"statementMap": {}, "s": {}, "branchMap": {}, "b": {}}}
        self.assertEqual(normalize("istanbul", data, "/source")["status"], "unsupported")

    def test_istanbul_implicit_else_is_preserved_at_mapped_condition(self):
        loc = {"start": {"line": 2, "column": 2}, "end": {"line": 2, "column": 27}}
        data = {"unit.js": {"statementMap": {}, "s": {}, "branchMap": {
            "0": {"type": "if", "loc": loc, "locations": [loc, {"start": {}, "end": {}}]}},
            "b": {"0": [1, 0]}}}
        result = normalize("istanbul", data, "/source")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["files"]["unit.js"]["branches"]["2:2:2:27:if:1:implicit-else"], 0)


class PlanTests(unittest.TestCase):
    def test_native_sanitizers_and_afl(self):
        self.assertIn("-fsanitize=address,undefined", build_plan("c", "asan-ubsan").flags)
        self.assertEqual(build_plan("cpp", "afl").environment["CXX"], "afl-clang-fast++")
        self.assertTrue(build_plan("c", "msan").limitations)

    def test_unknown_plan_cannot_silently_fallback(self):
        with self.assertRaises(ValueError):
            build_plan("rust", "ubsan")
        with self.assertRaises(ValueError):
            engine_argv("unknown", "driver", "corpus")

    def test_rust_branch_plan_explicitly_selects_nightly_without_changing_stable_plan(self):
        branch = build_plan("rust", "branch-coverage")
        self.assertIn("-Zcoverage-options=branch", branch.flags)
        self.assertEqual(branch.environment["RUSTUP_TOOLCHAIN"], "nightly-2026-10-01")
        self.assertNotIn("RUSTUP_TOOLCHAIN", build_plan("rust", "coverage").environment)

    def test_all_engines_bounded_argv(self):
        for engine in ("libfuzzer", "atheris", "afl", "cargo-fuzz", "go", "jazzer",
                       "hypothesis", "proptest", "rapid", "fast-check"):
            args = engine_argv(engine, "harness", "corpus", seconds=2, runs=10)
            self.assertIsInstance(args, list)
            self.assertNotIn("sh", args)
        with self.assertRaises(ValueError):
            engine_argv("go", "harness", "corpus", seconds=0)


class ReceiptTests(unittest.TestCase):
    def test_image_must_be_immutable(self):
        with self.assertRaises(ValueError):
            image("node:latest")
        self.assertEqual(image("sha256:" + "a" * 64), "sha256:" + "a" * 64)

    def receipt(self, capabilities):
        return build_receipt(
            language="rust", runner_image="sha256:" + "a" * 64,
            parent_task_image="rust@sha256:" + "b" * 64,
            source_sha256="c" * 64, build_sha256="d" * 64,
            features=["std"], instrumentation="tsan", flags=["-Zsanitizer=thread"],
            tools={"rustc": {"version": "pinned"}}, capabilities=capabilities)

    def test_unsupported_needs_reason(self):
        with self.assertRaises(ValueError):
            self.receipt({"tsan": {"status": "unsupported"}})
        data = self.receipt({"tsan": {"status": "unsupported", "reason": "runtime cannot map shadow memory"}})
        self.assertEqual(data["qualification"], "not-a-task-verdict")

    def test_validated_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.receipt({"tsan": {"status": "validated"}})
        with self.assertRaises(ValueError):
            self.receipt({"tsan": {"status": "passed"}})

    def test_missing_worker_coverage_stays_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = base / "raw.json"
            raw.write_text("{}")
            normalized = base / "normalized.json"
            normalized.write_text(json.dumps({"schema": "optimbench-coverage-v1", "status": "available", "limitations": []}))
            result = coverage_receipt(
                build_receipt_sha256="a" * 64, source_sha256="b" * 64,
                raw_paths=[raw], normalized_path=normalized, base=base,
                expected_workers=["parent", "child1"], observed_workers=["parent"])
            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["missing_workers"], ["child1"])
            self.assertEqual(result["raw"][0]["bytes"], 2)

    def test_artifacts_cannot_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            child = base / "child"
            child.mkdir()
            path = base / "outside"
            path.write_text("bad")
            with self.assertRaises(ValueError):
                artifact(path, child)


if __name__ == "__main__":
    unittest.main()
