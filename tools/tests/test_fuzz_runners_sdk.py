"""Real GCC/Clang controls; synthetic scope, not any task SDK qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.fuzz_runners.header_matrix import compare_cells, missing_sdk_evidence, run_cell, validate_manifest

PUBLIC = ("#ifndef SDK_CONTROL_H\n#define SDK_CONTROL_H\n"
          "#include <vendor_fixture/sdk_missing.h>\n"
          "static inline int api(void) { return 1; }\n#endif\n")


def cell(compiler):
    refs = [{"path": "support.txt", "sha256": hashlib.sha256(b"synthetic SDK control").hexdigest(),
             "quote": "Synthetic fixture's public header includes an optional third-party SDK; not task authority."}]
    return {"id": "sdk-control", "supported": True, "language": "c", "dialect": "c99",
            "compiler": compiler, "features": [], "headers": ["include/public.h"],
            "libraries": [], "include_dirs": ["include"], "symbol_refs": ["api"],
            "support_refs": refs, "include_context": {"id": "public", "includes": ["public.h"]},
            "optional_third_party_sdk_headers": [{"header": "vendor_fixture/sdk_missing.h", "support_refs": refs}],
            "translation_units": [
                {"name": "a.c", "source": "#include <public.h>\nint helper(void);\nint main(void){return api()+helper();}\n"},
                {"name": "b.c", "source": "#include <public.h>\nint helper(void){return api();}\n"}]}


class SdkManifestTests(unittest.TestCase):
    def test_sdk_designation_requires_frozen_independent_refs(self):
        value = cell("cc")
        value["optional_third_party_sdk_headers"][0]["support_refs"] = []
        with self.assertRaisesRegex(ValueError, "source references"):
            validate_manifest({"schema": "optimbench-header-matrix-v1", "required_cells": [value["id"]], "cells": [value]})

    def test_unresolved_cpp_support_remains_pending_not_sdk_na(self):
        value = {"id": "cpp-pending", "support_status": "pending", "supported": None,
                 "headers": ["public.h"], "reason": "C++ support authority unresolved",
                 "support_refs": cell("cc")["support_refs"]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = run_cell(value, root, root / "baseline")
            candidate = run_cell(value, root, root / "candidate")
            self.assertEqual(compare_cells(base, candidate)["attribution"], "support_evidence_pending")

    def test_missing_compiler_stays_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "include").mkdir()
            (root / "include/public.h").write_text(PUBLIC)
            value = cell("optimbench-nonexistent-compiler-control")
            base = run_cell(value, root, root / "baseline")
            candidate = run_cell(value, root, root / "candidate")
            self.assertEqual(compare_cells(base, candidate)["attribution"], "invalid_run")
            self.assertEqual(base["compile"], [])


class RealCompilerSdkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compilers = [name for name in ("gcc", "clang-16") if shutil.which(name)]
        if not cls.compilers or not shutil.which("nm"):
            raise unittest.SkipTest("real GNU/Clang compiler plus nm required; run in pinned native runner")
        prefix = os.environ.get("OPTIMBENCH_SDK_EVIDENCE")
        cls.temporary = None if prefix else tempfile.TemporaryDirectory()
        cls.root = Path(prefix) if prefix else Path(cls.temporary.name)
        cls.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.temporary:
            cls.temporary.cleanup()

    def pair(self, compiler, *, baseline_sdk=False, candidate_sdk=False,
             baseline_header=PUBLIC, candidate_header=PUBLIC, declared=True, context=False, primary_missing=False, nested=False):
        root = self.root / (self._testMethodName + "-" + compiler)
        root.mkdir()
        value = cell(compiler)
        if not declared:
            value.pop("optional_third_party_sdk_headers")
        if context:
            value["include_context"]["includes"].insert(0, "wrong_context.h")
            for tu in value["translation_units"]:
                tu["source"] = "#include <wrong_context.h>\n" + tu["source"]
        validate_manifest({"schema": "optimbench-header-matrix-v1", "required_cells": [value["id"]], "cells": [value]})
        results = []
        for arm, present, header in (("baseline", baseline_sdk, baseline_header),
                                     ("candidate", candidate_sdk, candidate_header)):
            build = root / (arm + "-build")
            (build / "include").mkdir(parents=True)
            if not primary_missing:
                if nested:
                    header = header.replace("<vendor_fixture/sdk_missing.h>", "<bridge.h>")
                    (build / "include/bridge.h").write_text("#include <vendor_fixture/sdk_missing.h>\n")
                (build / "include/public.h").write_text(header)
            if present:
                (build / "include/vendor_fixture").mkdir()
                (build / "include/vendor_fixture/sdk_missing.h").write_text("/* synthetic SDK */\n")
            results.append(run_cell(value, build, root / arm))
        result = compare_cells(*results)
        (root / "control.json").write_text(json.dumps({"scope": "synthetic controls only", "cell": value,
            "baseline": results[0], "candidate": results[1], "comparison": result}, sort_keys=True, indent=2) + "\n")
        return results, result, root

    def test_pristine_transitive_sdk_absence_is_positively_attributed(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                rows, result, _ = self.pair(compiler)
                self.assertEqual(result["attribution"], "missing_third_party_sdk")
                self.assertFalse(result["candidate_difference"])
                self.assertEqual(len(result["pristine_missing_sdk_evidence"]), 2)
                for proof in result["pristine_missing_sdk_evidence"]:
                    self.assertEqual(proof["missing_header"], "vendor_fixture/sdk_missing.h")
                    self.assertTrue(proof["requested_public_headers"])
                    self.assertTrue(proof["source_line_hex"])
                    self.assertEqual(proof["compiler_sha256"], rows[0]["compiler"]["sha256"])

    def test_pristine_pass_candidate_missing_sdk_is_real_compatibility_difference(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, baseline_sdk=True)
                self.assertEqual(result["attribution"], "candidate_compatibility_difference")
                self.assertTrue(result["candidate_difference"])

    def test_nested_include_chain_still_proves_requested_public_header_ancestor(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, nested=True)
                self.assertEqual(result["attribution"], "missing_third_party_sdk")
                for proof in result["pristine_missing_sdk_evidence"]:
                    self.assertTrue(proof["diagnostic_source"].endswith("/bridge.h"))
                    self.assertEqual(len(proof["include_chain"]), 2)

    def test_pristine_pass_candidate_syntax_failure_is_not_sdk_na(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, baseline_sdk=True, candidate_sdk=True,
                    candidate_header=PUBLIC + "\nthis is not valid C;\n")
                self.assertEqual(result["attribution"], "candidate_compatibility_difference")

    def test_pristine_syntax_failure_is_not_ignored(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, baseline_sdk=True, candidate_sdk=True,
                    baseline_header=PUBLIC + "\nthis is not valid C;\n")
                self.assertEqual(result["attribution"], "baseline_supported_cell_failed")

    def test_missing_primary_header_is_invalid_not_sdk_na(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, primary_missing=True)
                self.assertEqual(result["attribution"], "invalid_run")

    def test_wrong_include_context_is_not_sdk_na(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, context=True)
                self.assertEqual(result["attribution"], "baseline_supported_cell_failed")

    def test_undeclared_missing_dependency_is_not_guessed_to_be_optional_sdk(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                _, result, _ = self.pair(compiler, declared=False)
                self.assertEqual(result["attribution"], "baseline_supported_cell_failed")

    def test_truncated_or_other_errors_cannot_supply_sdk_proof(self):
        for compiler in self.compilers:
            with self.subTest(compiler=compiler):
                rows, _, root = self.pair(compiler)
                compiled = copy.deepcopy(rows[0]["compile"][0])
                compiled["logs"]["stderr"]["truncated"] = True
                self.assertIsNone(missing_sdk_evidence(cell(compiler), compiled, root / "baseline-build", root / "baseline"))
                # Exact log commitments must match; fabricated/changed errors fail closed.
                compiled = copy.deepcopy(rows[0]["compile"][0])
                compiled["logs"]["stderr"]["path"] = "tampered.stderr"
                (root / "baseline/tampered.stderr").write_text("syntax error instead\n")
                self.assertIsNone(missing_sdk_evidence(cell(compiler), compiled, root / "baseline-build", root / "baseline"))
                # Even an authenticated complete log cannot hide another error.
                compiled = copy.deepcopy(rows[0]["compile"][0])
                original = (root / "baseline" / compiled["logs"]["stderr"]["path"]).read_bytes()
                extra = original + b"\nextra.c:1:1: error: independent syntax failure\n"
                (root / "baseline/extra-error.stderr").write_bytes(extra)
                compiled["logs"]["stderr"] = {
                    "path": "extra-error.stderr", "bytes": len(extra), "truncated": False,
                    "sha256": hashlib.sha256(extra).hexdigest(), "full_sha256": hashlib.sha256(extra).hexdigest()}
                self.assertIsNone(missing_sdk_evidence(cell(compiler), compiled, root / "baseline-build", root / "baseline"))


if __name__ == "__main__":
    unittest.main()
