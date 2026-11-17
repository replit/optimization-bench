import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.fuzz_runners.header_control import control_manifest
from tools.fuzz_runners.header_matrix import compare_cells, validate_manifest, run_cell, dialect_flags, default_macros


class HeaderManifestTests(unittest.TestCase):
    def test_control_manifest_is_frozen_and_valid(self):
        data = control_manifest()
        self.assertEqual(validate_manifest(data), data)

    def test_c17_is_representable_without_forcing_older_standard(self):
        data = control_manifest()
        data["cells"][0]["dialect"] = "c17"
        # Syntax validation only. The task owner/auditor must independently
        # validate its real source-bound upstream support reference.
        self.assertEqual(validate_manifest(data)["cells"][0]["dialect"], "c17")

    def test_missing_cell_cannot_disappear(self):
        data = control_manifest()
        data["cells"].pop()
        with self.assertRaises(ValueError):
            validate_manifest(data)

    def test_support_cannot_be_guessed(self):
        data = control_manifest()
        data["cells"][0]["support_refs"] = []
        with self.assertRaises(ValueError):
            validate_manifest(data)

    def test_legal_include_context_required_in_both_tus(self):
        data = control_manifest()
        data["cells"][0]["translation_units"][0]["source"] = "int main(void){return 0;}"
        with self.assertRaises(ValueError):
            validate_manifest(data)

    def test_link_and_inline_bypasses_forbidden(self):
        for flag in ("-flto", "-Wl,--allow-multiple-definition", "-Wl,-z,muldefs", "-Wl,--gc-sections",
                     "-fgnu89-inline", "-Wl,--unresolved-symbols=ignore-all", "@extra.rsp", "-std=c11"):
            with self.subTest(flag=flag):
                data = control_manifest()
                data["cells"][0]["link_flags"] = [flag]
                with self.assertRaises(ValueError):
                    validate_manifest(data)

    def test_two_distinct_consumers_not_single_tu(self):
        for error in ("missing", "same"):
            data = control_manifest()
            units = data["cells"][0]["translation_units"]
            if error == "missing":
                units.pop()
            else:
                units[1]["source"] = units[0]["source"]
            with self.assertRaises(ValueError):
                validate_manifest(data)

    def test_symbol_references_required(self):
        data = control_manifest()
        data["cells"][0]["symbol_refs"] = []
        with self.assertRaises(ValueError):
            validate_manifest(data)

    def test_pending_dialect_support_is_not_unsupported_or_executed(self):
        data = control_manifest()
        cell = data["cells"][0]
        cell.update(supported=None, support_status="pending", reason="No supported dialect authority established")
        validate_manifest(data)
        with tempfile.TemporaryDirectory() as tmp, patch("tools.fuzz_runners.header_matrix._command") as command:
            row = run_cell(cell, Path(tmp) / "unused", Path(tmp) / "out")
        command.assert_not_called()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(compare_cells(row, row)["attribution"], "support_evidence_pending")
        self.assertIsNone(compare_cells(row, row)["candidate_verdict"])

    def test_pending_cannot_be_pretended_supported(self):
        data = control_manifest()
        data["cells"][0].update(support_status="pending", supported=True, reason="unknown")
        with self.assertRaises(ValueError):
            validate_manifest(data)

    def test_compiler_default_requires_explicit_upstream_default_flag_basis(self):
        data = control_manifest()
        cell = data["cells"][0]
        cell.update(dialect="compiler-default")
        with self.assertRaises(ValueError):
            validate_manifest(data)
        cell["support_basis"] = "pinned-upstream-default-flags"
        validate_manifest(data)
        self.assertEqual(dialect_flags(cell), [])
        self.assertEqual(dialect_flags({"dialect": "c99"}), ["-std=c99"])

    def test_default_macros_retained_as_raw_observations_not_minimum(self):
        macros = default_macros("#define __STDC_VERSION__ 202311L\n#define __GNUC_STDC_INLINE__ 1\n#define UNRELATED 7\n")
        self.assertEqual(macros, {"__STDC_VERSION__": "202311L", "__GNUC_STDC_INLINE__": "1"})
        self.assertNotIn("minimum_standard", macros)


class HeaderAttributionTests(unittest.TestCase):
    def cell(self):
        return {"id": "unit-gnu89", "status": "completed",
                "cell_sha256": "c" * 64,
                "compiler": {"sha256": "a" * 64}, "linker": {"sha256": "b" * 64},
                "compile": [{"exit_code": 0}, {"exit_code": 0}], "link": {"exit_code": 0}}

    def test_unchanged_valid_link_pair(self):
        self.assertEqual(compare_cells(self.cell(), self.cell())["attribution"], "same_success")

    def test_link_failure_is_compatibility_evidence(self):
        candidate = self.cell()
        candidate["link"]["exit_code"] = 1
        result = compare_cells(self.cell(), candidate)
        self.assertEqual(result["attribution"], "candidate_compatibility_difference")
        self.assertTrue(result["candidate_compile_succeeded"])
        self.assertFalse(result["candidate_link_succeeded"])

    def test_failed_pristine_supported_cell_is_blocked_not_candidate_failure(self):
        baseline = self.cell()
        baseline["compile"][0]["exit_code"] = 1
        baseline["link"] = None
        result = compare_cells(baseline, self.cell())
        self.assertEqual(result["attribution"], "baseline_supported_cell_failed")
        self.assertFalse(result["candidate_difference"])

    def test_toolchain_drift_is_invalid(self):
        candidate = self.cell()
        candidate["compiler"]["sha256"] = "c" * 64
        self.assertEqual(compare_cells(self.cell(), candidate)["attribution"], "invalid_run")

    def test_unsupported_not_silently_passed(self):
        record = {"id": "unsupported", "status": "unsupported", "reason": "documented unsupported", "cell_sha256": "c" * 64}
        self.assertEqual(compare_cells(record, record)["attribution"], "unsupported")

    def test_same_id_different_frozen_configuration_invalid(self):
        candidate = self.cell()
        candidate["cell_sha256"] = "d" * 64
        self.assertEqual(compare_cells(self.cell(), candidate)["attribution"], "invalid_run")


if __name__ == "__main__":
    unittest.main()
