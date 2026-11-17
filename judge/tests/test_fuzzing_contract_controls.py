"""Independent contract controls; not a claim of full v1.1 campaign adequacy.

These tests use the canonical controller, real Framework 2 hooks and existing
serde/NumPy comparators, not a second verdict or threshold implementation.
Actual task-adapter preconditions/campaigns remain pending in the fixture
catalog; controller callback unit controls do not certify worker isolation.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures") / "fuzzing-contract-controls"


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def npy(values, shape, dtype="<f8", fortran=False):
    """Small real .npy files using only stdlib, avoiding a host NumPy dependency."""
    header = repr({"descr": dtype, "fortran_order": fortran, "shape": tuple(shape)})
    # Version 1 requires header+10-byte preamble to be aligned; a newline ends it.
    text = header.encode("latin1")
    text += b" " * ((16 - ((10 + len(text) + 1) % 16)) % 16) + b"\n"
    kind = "d" if dtype.endswith("8") else "f"
    return (b"\x93NUMPY\x01\x00" + struct.pack("<H", len(text)) + text
            + struct.pack(dtype[0] + kind * len(values), *values))


class ContractControlCatalogTests(unittest.TestCase):
    def test_catalog_is_explicitly_partial_and_ids_are_unique(self):
        catalog = json.loads((FIXTURES / "controls.json").read_text())
        self.assertEqual(catalog["qualification_status"], "undertested")
        ids = [control["id"] for control in catalog["controls"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(control["integration_status"] for control in catalog["controls"]))

    def test_historical_sqlglot_recipe_is_the_private_missing_join_target(self):
        controls = {c["id"]: c for c in json.loads(
            (FIXTURES / "controls.json").read_text())["controls"]}
        private = controls["sqlglot-private-malformed-join"]
        self.assertEqual(private["surface_id"],
                         "sqlglot.parser.Parser._implicit_unnests_to_explicit")
        self.assertIn("tree.set('joins', [exp.Join()])", private["recipe"])
        self.assertEqual(private["expected"], {
            "admission": "rejected", "baseline_calls": 0, "candidate_calls": 0,
            "oracle_calls": 0, "candidate_verdict": None,
        })
        # Guard against broadening the exclusion to documented parser errors.
        public = controls["sqlglot-public-parser-error"]
        self.assertEqual(public["surface_id"], "sqlglot.parse_one")
        self.assertEqual(public["expected"]["admission"], "admitted")
        self.assertFalse(public["expected"]["oracle_equal"])
        # This is catalog validation; real task-adapter admission remains pending.
        self.assertIn("pending-task-adapter", private["integration_status"])

class NumpyMustNotFlagControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runner_path = str(ROOT / "tools/runner")
        original = list(sys.path)
        try:
            sys.path.insert(0, runner_path)
            cls.adapter = load("tasks/numpy-quantile/environment/workload/task.py",
                               "audit_protected_numpy")
            from runner import bundle
            cls.bundle = staticmethod(bundle)
        finally:
            sys.path[:] = original

    def equal(self, left, right):
        return self.adapter.compare(
            self.bundle({"result.npy": left}), self.bundle({"result.npy": right}))

    def test_equivalent_storage_order_is_not_a_semantic_difference(self):
        row_major = npy([1, 2, 3, 4], (2, 2))
        column_major = npy([1, 3, 2, 4], (2, 2), fortran=True)
        self.assertNotEqual(row_major, column_major)
        self.assertTrue(self.equal(row_major, column_major))

    def test_protected_tolerance_equal_nans_and_signed_zero_are_allowed(self):
        eps = sys.float_info.epsilon
        self.assertTrue(self.equal(npy([1], (1,)), npy([1 + 2 * eps], (1,))))
        self.assertTrue(self.equal(npy([float("nan"), -0.0], (2,)),
                                   npy([float("nan"), 0.0], (2,))))

    def test_shape_dtype_nan_and_outside_tolerance_changes_are_detected(self):
        eps = sys.float_info.epsilon
        base = npy([1, 2], (2,))
        for right in (
            npy([1, 2], (1, 2)), npy([1, 2], (2,), dtype="<f4"),
            npy([float("nan"), 2], (2,)), npy([1 + 64 * eps, 2], (2,)),
        ):
            with self.subTest(right=right):
                self.assertFalse(self.equal(base, right))


class HeaderConsumerCalibrationControls(unittest.TestCase):
    """Exercise real separate compile/link phases, not a second gate policy."""

    @classmethod
    def setUpClass(cls):
        cls.cc, cls.cxx = shutil.which("cc"), shutil.which("c++")
        if not cls.cc or not cls.cxx:
            raise RuntimeError("Header-consumer controls require cc and c++; unavailable is not pass")
        cls.headers = FIXTURES / "headers"

    def matrix_cell(self, header, dialect):
        compiler = self.cxx if dialect.startswith("c++") else self.cc
        with tempfile.TemporaryDirectory(prefix="fuzz-contract-header-") as temporary:
            root = Path(temporary)
            objects, compilations = [], []
            for name in ("first", "second"):
                target = root / (name + ".o")
                command = [
                    compiler, "-x", "c++" if dialect.startswith("c++") else "c",
                    "-std=" + dialect, "-O0", "-fno-inline", "-I" + str(self.headers),
                    '-DOPTIMBENCH_CONTROL_HEADER="' + header + '"',
                    "-c", str(self.headers / (name + ".c")), "-o", str(target),
                ]
                result = subprocess.run(command, capture_output=True, timeout=30)
                compilations.append({"argv": command, "code": result.returncode,
                                     "stderr": result.stderr.decode(errors="replace")})
                if result.returncode == 0:
                    objects.append(target)
            result = {"compilations": compilations, "link": None, "run": None}
            if len(objects) != 2:
                return result
            library = root / "library.o"
            command = [self.cc, "-std=c99", "-O0", "-c",
                       str(self.headers / "library.c"), "-o", str(library)]
            built = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr.decode(errors="replace"))
            executable = root / "consumer"
            command = [compiler, *(str(p) for p in objects), str(library),
                       "-o", str(executable)]
            linked = subprocess.run(command, capture_output=True, timeout=30)
            result["link"] = {"argv": command, "code": linked.returncode,
                              "stderr": linked.stderr.decode(errors="replace")}
            if linked.returncode == 0:
                result["run"] = subprocess.run([str(executable)], timeout=10).returncode
            return result

    def assert_compiled(self, cell):
        self.assertEqual(len(cell["compilations"]), 2)
        for unit in cell["compilations"]:
            self.assertEqual(unit["code"], 0, unit["stderr"])

    def test_unchanged_fixture_public_header_supported_cells_compile_link_run(self):
        for dialect in ("gnu89", "c99", "c11", "c17", "c++11", "c++17"):
            with self.subTest(dialect=dialect):
                cell = self.matrix_cell("public.h", dialect)
                self.assert_compiled(cell)
                self.assertEqual(cell["link"]["code"], 0, cell["link"]["stderr"])
                self.assertEqual(cell["run"], 0)

    def test_gnu89_inline_control_compiles_both_units_but_fails_combined_link(self):
        cell = self.matrix_cell("gnu-inline-mutant.h", "gnu89")
        self.assert_compiled(cell)
        self.assertNotEqual(cell["link"]["code"], 0)
        self.assertIn("contract_value", cell["link"]["stderr"])
        self.assertRegex(cell["link"]["stderr"], r"multiple definition|duplicate symbol")
        self.assertIsNone(cell["run"])

    def test_the_same_inline_control_passes_c99_with_fixed_external_library(self):
        cell = self.matrix_cell("gnu-inline-mutant.h", "c99")
        self.assert_compiled(cell)
        self.assertEqual(cell["link"]["code"], 0, cell["link"]["stderr"])
        self.assertEqual(cell["run"], 0)

    def test_cpp_linkage_change_is_not_hidden_by_successful_compilation(self):
        cell = self.matrix_cell("linkage-mutant.h", "c++17")
        self.assert_compiled(cell)
        self.assertNotEqual(cell["link"]["code"], 0)
        self.assertIn("contract_value", cell["link"]["stderr"])
        self.assertRegex(cell["link"]["stderr"], r"undefined reference|undefined symbol")

    def test_legal_umbrella_passes_but_private_child_include_is_baseline_invalid(self):
        legal = self.matrix_cell("umbrella.h", "c17")
        self.assert_compiled(legal)
        self.assertEqual(legal["link"]["code"], 0, legal["link"]["stderr"])
        self.assertEqual(legal["run"], 0)
        invalid = self.matrix_cell("detail.h", "c17")
        self.assertTrue(all(unit["code"] != 0 for unit in invalid["compilations"]))
        self.assertIsNone(invalid["link"])
        self.assertIsNone(invalid["run"])


class Framework2HeaderIntegrationControls(unittest.TestCase):
    """Exercise the actual shared runner; Framework 2 is a declared dependency."""

    @classmethod
    def setUpClass(cls):
        from tools.fuzz_runners import header_matrix
        cls.runner = header_matrix
        cls.headers = FIXTURES / "headers"
        cls.cc, cls.cxx = shutil.which("cc"), shutil.which("c++")
        if not cls.cc or not cls.cxx:
            raise RuntimeError("Real header runner controls require cc and c++")

    def cell(self, dialect="c11", include="public.h"):
        supported = (self.headers / "README.md").read_bytes()
        language = "c++" if dialect.startswith("c++") else "c"
        compiler = self.cxx if language == "c++" else self.cc
        cell = {
            "id": "independent-fixture", "supported": True,
            "language": language, "dialect": dialect, "features": [],
            "support_refs": [{
                "path": "README.md", "sha256": hashlib.sha256(supported).hexdigest(),
                "quote": "Authored synthetic fixture support only; no upstream task support inferred",
            }],
            "headers": ["public.h"] if include == "public.h" else [include, "public.h"],
            "include_context": {"id": "authored-fixture-context", "includes": [include]},
            "compiler": compiler, "include_dirs": ["."], "defines": [],
            "compile_flags": [], "link_flags": [], "libraries": ["library.o"],
            "symbol_refs": ["contract_value"],
            "translation_units": [
                {"name": name + ".c",
                 "source": (self.headers / (name + ".c")).read_text().replace(
                     "#include OPTIMBENCH_CONTROL_HEADER", '#include "' + include + '"')}
                for name in ("first", "second")
            ],
        }
        self.runner.validate_manifest({
            "schema": self.runner.SCHEMA,
            "required_cells": [cell["id"]], "cells": [cell],
        })
        return cell

    def pair(self, candidate_header=None, *, dialect="c11", include="public.h",
             candidate_features=None):
        cell = self.cell(dialect, include)
        with tempfile.TemporaryDirectory(prefix="fuzz-shared-header-control-") as temporary:
            root = Path(temporary)
            records, logs = [], []
            for arm in ("baseline", "candidate"):
                build = root / arm
                shutil.copytree(self.headers, build)
                if arm == "candidate" and candidate_header:
                    shutil.copyfile(self.headers / candidate_header, build / "public.h")
                built = subprocess.run(
                    [self.cc, "-std=c99", "-O0", "-c", str(build / "library.c"),
                     "-o", str(build / "library.o")], capture_output=True, timeout=30)
                self.assertEqual(built.returncode, 0, built.stderr.decode(errors="replace"))
                arm_cell = copy.deepcopy(cell)
                if arm == "candidate" and candidate_features is not None:
                    arm_cell["features"] = candidate_features
                output = root / (arm + "-evidence")
                record = self.runner.run_cell(arm_cell, build, output)
                records.append(record)
                logs.append((output / "link.stderr").read_text()
                            if (output / "link.stderr").is_file() else "")
            return records[0], records[1], self.runner.compare_cells(*records), logs

    def test_shared_runner_preserves_two_tu_success_and_evidence(self):
        baseline, candidate, result, _ = self.pair()
        self.assertEqual(result["attribution"], "same_success")
        self.assertFalse(result["candidate_difference"])
        for record in (baseline, candidate):
            self.assertEqual(len(record["compile"]), 2)
            self.assertEqual(len(record["translation_units"]), 2)
            self.assertTrue(all(unit["exit_code"] == 0 for unit in record["compile"]))
            self.assertEqual(record["link"]["exit_code"], 0)
            self.assertTrue(all(len(unit["object_sha256"]) == 64 for unit in record["compile"]))
            self.assertEqual(len(record["headers"][0]["sha256"]), 64)

    def test_shared_runner_detects_gnu89_link_failure_not_compile_failure(self):
        baseline, candidate, result, logs = self.pair("gnu-inline-mutant.h", dialect="gnu89")
        self.assertEqual(result["attribution"], "candidate_compatibility_difference")
        self.assertTrue(result["candidate_difference"])
        self.assertTrue(all(unit["exit_code"] == 0 for unit in candidate["compile"]))
        self.assertEqual(baseline["link"]["exit_code"], 0)
        self.assertNotEqual(candidate["link"]["exit_code"], 0)
        self.assertRegex(logs[1], r"multiple definition|duplicate symbol")

    def test_shared_runner_same_inline_header_passes_c99(self):
        _, _, result, _ = self.pair("gnu-inline-mutant.h", dialect="c99")
        self.assertEqual(result["attribution"], "same_success")

    def test_shared_runner_supported_c17_cell_is_representable(self):
        _, _, result, _ = self.pair(dialect="c17")
        self.assertEqual(result["attribution"], "same_success")

    def test_shared_runner_cpp_linkage_loss_is_candidate_link_difference(self):
        _, candidate, result, _ = self.pair("linkage-mutant.h", dialect="c++17")
        self.assertTrue(all(unit["exit_code"] == 0 for unit in candidate["compile"]))
        self.assertEqual(result["attribution"], "candidate_compatibility_difference")

    def test_shared_runner_illegal_include_is_baseline_failure_not_candidate_defect(self):
        baseline, _, result, _ = self.pair(include="detail.h")
        self.assertIsNone(baseline["link"])
        self.assertEqual(result["attribution"], "baseline_supported_cell_failed")
        self.assertFalse(result["candidate_difference"])

    def test_shared_runner_unsupported_dialect_never_invokes_a_compiler(self):
        cell = self.cell()
        cell.update(supported=False, dialect="c++03", reason="Outside authored fixture support declaration")
        self.runner.validate_manifest({
            "schema": self.runner.SCHEMA, "required_cells": [cell["id"]], "cells": [cell]})
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(self.runner, "_command", side_effect=AssertionError("unexpected compile")):
            left = self.runner.run_cell(cell, Path(temporary) / "unused", Path(temporary) / "left")
            right = self.runner.run_cell(cell, Path(temporary) / "unused", Path(temporary) / "right")
        self.assertEqual(self.runner.compare_cells(left, right)["attribution"], "unsupported")

    def test_shared_runner_same_id_with_different_frozen_cell_is_invalid(self):
        _, _, result, _ = self.pair(candidate_features=["different-feature"])
        self.assertEqual(result["attribution"], "invalid_run")


class Framework2CoverageReviewControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.fuzz_runners import coverage, receipt
        cls.collector, cls.receipts = coverage, receipt

    def llvm(self):
        return {
            "type": "llvm.coverage.json.export",
            "data": [{"files": [{
                "filename": "/source/changed.c",
                "segments": [[4, 1, 7, True, True, False], [5, 1, 0, False, False, False]],
                "branches": [[4, 2, 4, 8, 7, 0]],
            }]}],
        }

    def test_executed_changed_line_does_not_cover_unexecuted_branch(self):
        normalized = self.collector.normalize("llvm", self.llvm(), "/source")
        source = normalized["files"]["changed.c"]
        self.assertEqual(source["lines"]["4"], 7)
        self.assertEqual(source["branches"]["4:2:4:8:true"], 7)
        self.assertEqual(source["branches"]["4:2:4:8:false"], 0)

    def test_truncated_python_branch_inventory_is_not_available(self):
        data = {"meta": {"branch_coverage": True}, "files": {"changed.py": {
            "executed_lines": [1], "missing_lines": [2]}}}
        try:
            result = self.collector.normalize("python", data, "/source")
        except self.collector.CoverageError:
            return
        self.assertNotEqual(result["status"], "available")

    def test_non_task_header_hits_do_not_become_candidate_coverage(self):
        data = self.llvm()
        data["data"][0]["files"][0]["filename"] = "/usr/include/other.h"
        normalized = self.collector.normalize("llvm", data, "/source")
        self.assertEqual(normalized["files"], {})
        self.assertEqual(normalized["status"], "unsupported")

    def test_missing_child_export_is_unavailable_not_full_worker_coverage(self):
        normalized = self.collector.normalize("llvm", self.llvm(), "/source")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw.json"
            raw.write_text(json.dumps(self.llvm()))
            path = root / "normalized.json"
            path.write_text(json.dumps(normalized))
            record = self.receipts.coverage_receipt(
                build_receipt_sha256="a" * 64, raw_paths=[raw],
                normalized_path=path, base=root,
                expected_workers=["main", "helper"], observed_workers=["main"],
                source_sha256="b" * 64)
        self.assertEqual(record["missing_workers"], ["helper"])
        self.assertEqual(record["status"], "unsupported")
        self.assertEqual(record["qualification"], "not-a-task-verdict")


class OwnershipObservationControls(unittest.TestCase):
    """Calibrate legal ownership evidence without equating identity with value."""

    @classmethod
    def setUpClass(cls):
        cls.probe = load(
            "judge/tests/fixtures/fuzzing-contract-controls/ownership_probe.py",
            "audit_ownership_probe")

    def observe_mutable(self, factory, interleave=None):
        return self.probe.observe(
            factory, snapshot=lambda value: value,
            mutate=lambda value: value["slot"].update(spacing="touch"),
            interleave=interleave, views=lambda value: {"slot": value["slot"]})

    def fresh(self):
        return {"slot": {"spacing": "single"}}

    def test_owned_mutable_result_mutation_does_not_pollute_independent_getters(self):
        value = self.observe_mutable(self.fresh)
        self.assertEqual(
            [item["slot"]["spacing"] for item in value["effects"]["after_refetch"]],
            ["touch", "single", "single"])
        self.assertFalse(value["diagnostics"]["root_aliases"][0][1])
        self.assertFalse(value["diagnostics"]["view_aliases"]["slot"][0][1])

    def test_shared_mutable_cache_changes_semantics_not_only_identity(self):
        shared = self.fresh()
        observed = self.observe_mutable(lambda: shared)
        correct = self.observe_mutable(self.fresh)
        self.assertNotEqual(observed["effects"], correct["effects"])
        self.assertEqual(
            [item["slot"]["spacing"] for item in observed["effects"]["after_refetch"]],
            ["touch", "touch", "touch"])
        self.assertTrue(observed["diagnostics"]["root_aliases"][0][1])

    def test_shallow_copy_cannot_hide_shared_mutable_descendants(self):
        shared = self.fresh()
        observed = self.observe_mutable(lambda: dict(shared))
        correct = self.observe_mutable(self.fresh)
        self.assertFalse(observed["diagnostics"]["root_aliases"][0][1])
        self.assertTrue(observed["diagnostics"]["view_aliases"]["slot"][0][1])
        self.assertNotEqual(observed["effects"], correct["effects"])

    def test_immutable_interning_without_identity_promise_is_not_a_value_difference(self):
        shared = (7,)
        interned = self.probe.observe(lambda: shared, snapshot=lambda value: value)
        fresh = self.probe.observe(lambda: tuple([7]), snapshot=lambda value: value)
        self.assertEqual(interned["effects"], fresh["effects"])
        self.assertNotEqual(interned["diagnostics"], fresh["diagnostics"])

    def test_live_retained_results_are_checked_between_interleave_and_restore(self):
        shared = self.fresh()

        def restore_and_get():
            shared["slot"]["spacing"] = "single"
            return shared

        observed = self.probe.observe(
            restore_and_get, snapshot=lambda value: value,
            interleave=lambda: shared["slot"].update(spacing="other"))
        # Refetch restores the original bytes; checking only final serialization
        # would miss the intervening corruption of a still-live prior result.
        self.assertEqual(observed["effects"]["after_interleave"][0]["slot"]["spacing"], "other")
        self.assertEqual(observed["effects"]["after_refetch"][0]["slot"]["spacing"], "single")

    def test_documented_borrowed_live_view_is_not_forced_to_independent_copy(self):
        state = self.fresh()
        observed = self.observe_mutable(lambda: state)
        self.assertEqual(
            [item["slot"]["spacing"] for item in observed["effects"]["after_refetch"]],
            ["touch", "touch", "touch"])
        # This fixture reports behavior, not a hardcoded fresh-allocation rule.
        # A task whose contract grants a borrowed live view compares it as such.
        self.assertNotIn("verdict", observed)


class SharedControllerContractControls(unittest.TestCase):
    """Real canonical policy hooks, with explicit verifier callback spies.

    These unit observers do not certify process isolation or task-adapter
    preconditions. The SQLGlot task owner must still supply its real hooks.
    """

    @classmethod
    def setUpClass(cls):
        from judge import fuzzing_contract
        cls.contract = fuzzing_contract
        cls.catalog = {c["id"]: c for c in json.loads(
            (FIXTURES / "controls.json").read_text())["controls"]}
        cls.builds = {"baseline": "b" * 64, "candidate": "c" * 64}

    def case(self, surface_id="sqlglot.parse_one", sql="SELECT 1 AS a"):
        return {
            "task": "sqlglot-mozilla-transpile", "surface_id": surface_id,
            "contract_id": "sqlglot-public-exact-output-errors",
            "precondition_id": "public-sql-domain", "input": {"sql": sql},
            "options": {"read": "bigquery", "write": "duckdb"}, "sequence": [], "faults": [],
        }

    def surface(self, case, visibility="public"):
        return {
            "id": case["surface_id"], "visibility": visibility,
            "contract_id": case["contract_id"], "precondition_id": case["precondition_id"],
            "generators": ["independent-control"], "oracle_policy": "exact-contract-v1",
            "oracle": {"baseline_terminations": ["return", "exception"],
                       "baseline_exit_codes": [0],
                       "required_error_fields": {"class": "nonempty_string", "message": "string"}},
        }

    def observation(self, case, arm, *, outcome=None, effects=None, diagnostics=None):
        return {
            "format": "optimbench-fuzz-observation-v1",
            "identity": {"task": case["task"], "surface_id": case["surface_id"],
                         "case_sha256": self.contract.digest(case),
                         "build_sha256": self.builds[arm]},
            "termination": {"kind": "return", "exit_code": 0},
            "outcome": outcome if outcome is not None else {"kind": "ok", "value": "SELECT 1 AS a"},
            "effects": effects if effects is not None else {},
            "diagnostics": diagnostics if diagnostics is not None else {},
        }

    def evaluate(self, case, observe, **kwargs):
        return self.contract.evaluate_case(
            case, self.surface(case), lambda *_: {"valid": True, "reason": "unit control domain"},
            observe, self.builds, repetitions=2, **kwargs)

    def test_historical_private_join_recipe_rejected_before_all_callbacks(self):
        from unittest.mock import Mock
        fixture = self.catalog["sqlglot-private-malformed-join"]
        case = self.case(fixture["surface_id"])
        case["input"] = {"recipe": fixture["recipe"], "missing_join_this": True}
        precondition = Mock(side_effect=AssertionError("private precondition invoked"))
        observer = Mock(side_effect=AssertionError("private arm invoked"))
        oracle = Mock(side_effect=AssertionError("private oracle invoked"))
        result = self.contract.evaluate_case(
            case, self.surface(case, "private"), precondition, observer,
            self.builds, compare_hook=oracle)
        self.assertEqual(result["status"], "out_of_contract")
        self.assertFalse(result["admitted"])
        self.assertIsNone(result["candidate_verdict"])
        precondition.assert_not_called()
        observer.assert_not_called()
        oracle.assert_not_called()

    def test_public_symbol_does_not_admit_manually_invalid_join_state(self):
        from unittest.mock import Mock
        case = self.case("sqlglot.expressions.Expression.sql")
        case["input"] = {"builder": "select-from-bare-join", "join_this": None}
        precondition = Mock(return_value={
            "valid": False, "reason": "Join requires a target; parser-valid AST domain"})
        observer = Mock(side_effect=AssertionError("invalid public AST executed"))
        oracle = Mock(side_effect=AssertionError("invalid public AST compared"))
        result = self.contract.evaluate_case(
            case, self.surface(case), precondition, observer, self.builds, compare_hook=oracle)
        self.assertEqual(result["status"], "out_of_contract")
        self.assertIsNone(result["candidate_verdict"])
        precondition.assert_called_once_with(case, self.surface(case))
        observer.assert_not_called()
        oracle.assert_not_called()

    def test_valid_public_sql_semantic_mutant_is_still_detected_twice(self):
        case = self.case()
        calls = []

        def observe(value, arm, index):
            calls.append((arm, index))
            return self.observation(value, arm, outcome={
                "kind": "ok", "value": "SELECT 1 AS a" if arm == "baseline" else "SELECT 2 AS a"})

        result = self.evaluate(case, observe)
        self.assertTrue(result["admitted"])
        self.assertEqual(calls, [("baseline", 0), ("candidate", 0),
                                 ("baseline", 1), ("candidate", 1)])
        self.assertIn({"kind": "contract_regression", "reproduced": True}, result["findings"])
        self.assertTrue(all(not comparison["equal"] for comparison in result["comparisons"]))

    def test_documented_parser_error_is_admitted_and_exact_type_message_are_kept(self):
        case = self.case(sql="SELECT (")
        baseline_error = {"kind": "error", "class": "ParseError", "message": "Expected expression"}

        def observe(value, arm, index):
            result = self.observation(value, arm, outcome=baseline_error)
            result["termination"]["kind"] = "exception"
            return result

        same = self.evaluate(case, observe)
        self.assertTrue(same["admitted"])
        self.assertEqual(same["findings"], [])
        for changed in ({**baseline_error, "message": "Different wording"},
                        {**baseline_error, "class": "ValueError"}):
            with self.subTest(changed=changed):
                def changed_observe(value, arm, index):
                    result = observe(value, arm, index)
                    if arm == "candidate":
                        result["outcome"] = changed
                    return result
                result = self.evaluate(case, changed_observe)
                self.assertIn({"kind": "contract_regression", "reproduced": True},
                              result["findings"])

    def test_json_boolean_does_not_equal_json_number_in_typed_exact_oracle(self):
        case = self.case()

        def observe(value, arm, index):
            return self.observation(value, arm, outcome={
                "kind": "ok", "value": True if arm == "baseline" else 1})

        result = self.evaluate(case, observe)
        self.assertIn({"kind": "contract_regression", "reproduced": True}, result["findings"])

    def test_missing_promised_error_fields_in_both_arms_is_invalid_before_oracle(self):
        from unittest.mock import Mock
        case = self.case(sql="SELECT (")
        for outcome in ({"kind": "error"}, {"kind": "error", "class": "ParseError"},
                        {"kind": "error", "class": "", "message": "bad"},
                        {"kind": "error", "class": 1, "message": "bad"}):
            with self.subTest(outcome=outcome):
                oracle = Mock(side_effect=AssertionError("incomplete error compared"))
                with self.assertRaises(self.contract.ContractError):
                    self.evaluate(
                        case, lambda value, arm, index: self.observation(value, arm, outcome=outcome),
                        compare_hook=oracle)
                oracle.assert_not_called()

    def test_explicitly_promised_empty_error_message_is_a_valid_string(self):
        case = self.case(sql="SELECT (")
        result = self.evaluate(
            case, lambda value, arm, index: self.observation(
                value, arm, outcome={"kind": "error", "class": "ParseError", "message": ""}))
        self.assertTrue(result["admitted"])
        self.assertEqual(result["findings"], [])

    def test_identity_and_call_diagnostics_alone_do_not_create_a_difference(self):
        case = self.case()

        def observe(value, arm, index):
            return self.observation(value, arm, diagnostics={
                "root_aliases": [[True, arm == "candidate"], [arm == "candidate", True]],
                "write_call_count": 1 if arm == "baseline" else 7,
            })

        self.assertEqual(self.evaluate(case, observe)["findings"], [])

    def test_canonical_oracle_detects_shared_descendants_despite_distinct_roots(self):
        case = self.case("fixture.owned_mutable_factory")
        case.update(task="owned-factory-control", contract_id="caller-owned-mutable",
                    precondition_id="legal-mutation")
        probe = load("judge/tests/fixtures/fuzzing-contract-controls/ownership_probe.py",
                     "controller_owned_observation")

        def observe(value, arm, index):
            shared = {"slot": {"spacing": "single"}}
            factory = (lambda: {"slot": {"spacing": "single"}}) if arm == "baseline" else (
                lambda: dict(shared))
            captured = probe.observe(
                factory, snapshot=lambda item: item,
                mutate=lambda item: item["slot"].update(spacing="touch"),
                views=lambda item: {"slot": item["slot"]})
            return self.observation(value, arm, outcome={"kind": "ok", "value": None},
                                    **captured)

        result = self.evaluate(case, observe)
        candidate = result["observations"]["candidate"][0]
        self.assertFalse(candidate["diagnostics"]["root_aliases"][0][1])
        self.assertTrue(candidate["diagnostics"]["view_aliases"]["slot"][0][1])
        self.assertIn({"kind": "contract_regression", "reproduced": True}, result["findings"])

    def thread_observer(self, leak):
        def observe(value, arm, index):
            during, at_boundary = 0, 0
            if arm == "candidate":
                stop = threading.Event()
                worker = threading.Thread(target=stop.wait)
                worker.start()
                try:
                    during = int(worker.is_alive())
                    self.assertEqual(during, 1)
                    if not leak:
                        stop.set()
                        worker.join(timeout=2)
                    # This is the API-return boundary; cleanup below is the
                    # verifier fixture's cleanup, not candidate library work.
                    at_boundary = int(worker.is_alive())
                finally:
                    stop.set()
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
            return self.observation(
                value, arm, effects={"owned_live_workers_at_api_return": at_boundary},
                diagnostics={"owned_workers_during_call": during})
        return observe

    def test_real_joined_transient_worker_does_not_flag_canonical_controller(self):
        self.assertEqual(self.evaluate(self.case(), self.thread_observer(leak=False))["findings"], [])

    def test_worker_alive_at_return_is_detected_and_verifier_fixture_cleans_it(self):
        result = self.evaluate(self.case(), self.thread_observer(leak=True))
        self.assertIn({"kind": "contract_regression", "reproduced": True}, result["findings"])

    def test_open_owned_fd_at_return_is_not_mistaken_for_diagnostic_activity(self):
        case = self.case()

        def observe(value, arm, index):
            handle = tempfile.TemporaryFile()
            try:
                if arm == "baseline":
                    handle.close()
                at_boundary = int(not handle.closed)
            finally:
                handle.close()
            return self.observation(value, arm, effects={"owned_open_fds_at_api_return": at_boundary})

        result = self.evaluate(case, observe)
        self.assertIn({"kind": "contract_regression", "reproduced": True}, result["findings"])

    def test_report_only_memory_is_not_a_hidden_failure_or_missing_gate(self):
        obligations = dict.fromkeys(self.contract.REQUIRED_OBLIGATIONS, True)
        result = self.contract.decide([], obligations, [
            {"unit": "high", "baseline": 1, "candidate": 1000000},
            {"unit": "unavailable", "baseline": None, "candidate": None},
        ])
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["claimable"])
        self.assertFalse(result["timing_performed"])
        self.assertFalse(result["peak_memory"]["verdict_impact"])

    def test_coverage_gap_does_not_hide_a_reproduced_semantic_regression(self):
        case = self.case()

        def observe(value, arm, index):
            return self.observation(value, arm, effects={
                "accepted_bytes_hex": "6162" if arm == "baseline" else ""})

        finding = self.evaluate(case, observe)
        obligations = {name: name != "changed_coverage"
                       for name in self.contract.REQUIRED_OBLIGATIONS}
        result = self.contract.decide(finding["findings"], obligations)
        self.assertEqual(result["status"], "contract_regression")
        self.assertIn("changed_coverage", result["missing_obligations"])

    def test_go_panic_frames_are_diagnostic_but_headline_and_exit_remain_semantic(self):
        case = self.case("fixture.go_cli_panic")
        left = self.observation(
            case, "baseline", outcome={"kind": "error", "class": "GoPanic", "headline": "panic: invalid input"},
            diagnostics={"go_panic_frames": "main.old\n /old/source.go:10 +0x1234"})
        left["termination"] = {"kind": "panic", "exit_code": 2}
        right = copy.deepcopy(left)
        right["diagnostics"]["go_panic_frames"] = "main.new\n /new/source.go:90 +0x9876"
        self.assertTrue(self.contract.exact_contract_compare(left, right, case)["equal"])
        right["outcome"]["headline"] = "panic: different error"
        self.assertFalse(self.contract.exact_contract_compare(left, right, case)["equal"])
        right["outcome"] = copy.deepcopy(left["outcome"])
        right["termination"]["exit_code"] = 0
        self.assertFalse(self.contract.exact_contract_compare(left, right, case)["equal"])
        # This tests the reviewed representation, not a task stderr parser or
        # permission to admit every pristine panic as a qualified public case.


class CliOutputFaultCalibrationControls(unittest.TestCase):
    """Real ENOSPC/EPIPE/EBADF and flush/write failure calibration on Linux."""

    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which("cc")
        if not cls.cc:
            raise RuntimeError("CLI output-fault controls require a C compiler")
        if not Path("/dev/full").exists():
            raise RuntimeError("ENOSPC calibration requires /dev/full; unavailable is not pass")
        cls.temporary = tempfile.TemporaryDirectory(prefix="fuzz-cli-output-controls-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binaries = {}
        for arm, ignore, early_rewrite in (
            ("baseline", 0, 0), ("candidate", 1, 0), ("side_effect_candidate", 0, 1),
        ):
            target = Path(cls.temporary.name) / arm
            result = subprocess.run(
                [cls.cc, "-std=c99", "-O0", f"-DIGNORE_OUTPUT_ERRORS={ignore}",
                 f"-DREWRITE_BEFORE_FLUSH={early_rewrite}",
                 str(FIXTURES / "cli-output.c"), "-o", str(target)],
                capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
            cls.binaries[arm] = target

    def observe(self, arm, fault, size="small", input_path=None):
        argv = [str(self.binaries[arm]), "closed" if fault == "closed" else size]
        if input_path is not None:
            argv.append(str(input_path))
        environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        if fault == "full":
            with open("/dev/full", "wb", buffering=0) as sink:
                result = subprocess.run(argv, stdout=sink, stderr=subprocess.PIPE,
                                        env=environment, timeout=10)
        elif fault == "pipe":
            read, write = os.pipe()
            os.close(read)  # No reader exists before the child starts.
            try:
                result = subprocess.run(argv, stdout=write, stderr=subprocess.PIPE,
                                        env=environment, timeout=10)
            finally:
                os.close(write)
        else:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=environment, timeout=10)
        return result

    def assert_fault_detected(self, fault, size="small", stage=None):
        baseline = self.observe("baseline", fault, size)
        mutant = self.observe("candidate", fault, size)
        self.assertEqual(baseline.returncode, 2, baseline.stderr)
        self.assertEqual(mutant.returncode, 0, mutant.stderr)
        self.assertTrue(baseline.stderr)
        self.assertEqual(mutant.stderr, b"")
        if stage:
            self.assertTrue(baseline.stderr.startswith(stage + b":"), baseline.stderr)

    def test_dev_full_small_payload_reaches_flush_only_error(self):
        self.assert_fault_detected("full", stage=b"flush")

    def test_dev_full_large_payload_reaches_write_error(self):
        self.assert_fault_detected("full", size="large", stage=b"write")

    def test_closed_pipe_reader_reports_epipe_without_inband_observation_channel(self):
        self.assert_fault_detected("pipe", stage=b"flush")

    def test_closed_stdout_reports_bad_fd_instead_of_silent_success(self):
        self.assert_fault_detected("closed", stage=b"flush")

    def test_same_exit_and_error_cannot_hide_file_mutation_after_stdout_failure(self):
        from judge import fuzzing_contract
        outputs, files = [], []
        with tempfile.TemporaryDirectory() as temporary:
            for arm in ("baseline", "side_effect_candidate"):
                path = Path(temporary) / (arm + ".sql")
                path.write_bytes(b"original\n")
                outputs.append(self.observe(arm, "full", input_path=path))
                files.append(path.read_bytes())
        self.assertEqual([value.returncode for value in outputs], [2, 2])
        self.assertEqual(outputs[0].stderr, outputs[1].stderr)
        self.assertEqual(files[0], b"original\n")
        self.assertEqual(files[1], b"rewritten\n")
        left = {"termination": {"kind": "return", "exit_code": 2},
                "outcome": {"kind": "error", "stderr_hex": outputs[0].stderr.hex()},
                "effects": {"input_file_hex": files[0].hex()}, "diagnostics": {}}
        right = {**left, "effects": {"input_file_hex": files[1].hex()}}
        self.assertFalse(fuzzing_contract.exact_contract_compare(left, right, {})["equal"])
