"""Shared contract primitives: fail closed without rewriting v1.0 scoring."""
import copy
import unittest

from judge import fuzzing_contract as f

H = "a" * 64
B = "b" * 64
C = "c" * 64


def identity(task="example"):
    return {field: task if field == "task" else H for field in f.IDENTITY_FIELDS}


def case():
    return {"task": "example", "surface_id": "example.public", "contract_id": "public-contract",
            "precondition_id": "valid-input", "input": "", "options": {}, "sequence": [], "faults": []}


def surface():
    return {"id": "example.public", "visibility": "public", "contract_id": "public-contract",
            "precondition_id": "valid-input", "generators": ["grammar"], "oracle_policy": "exact",
            "oracle": {"required_error_fields": {"class": "nonempty_string", "message": "string"}}}


def observation(value, arm, outcome=None):
    return {"format": "optimbench-fuzz-observation-v1",
            "identity": {"task": value["task"], "surface_id": value["surface_id"],
                         "case_sha256": f.case_digest(value), "build_sha256": B if arm == "baseline" else C},
            "termination": {"kind": "return", "exit_code": 0},
            "outcome": outcome or {"kind": "ok", "value": "complete"},
            "effects": {"accepted_bytes_hex": "6162"}, "diagnostics": {"chunk_count": 99}}


class ContractExecutionTests(unittest.TestCase):
    def test_optional_self_hash_is_bound_without_recursive_hashing(self):
        value = case()
        expected = f.case_digest(value)
        value["case_sha256"] = expected
        self.assertEqual(f.case_digest(value), expected)
        f.validate_observation(observation(value, "baseline"), value, B)
        value["input"] = {"case_sha256": "payload-field-not-metadata"}
        with self.assertRaisesRegex(f.ContractError, "self-hash"):
            f.case_digest(value)
        value.pop("case_sha256")
        self.assertNotEqual(f.case_digest(value), expected)

    def run_case(self, observe, **kwargs):
        return f.evaluate_case(case(), surface(), lambda *_: {"valid": True, "reason": "valid"},
                               observe, {"baseline": B, "candidate": C}, **kwargs)

    def test_private_helper_is_rejected_before_observer_and_oracle(self):
        def forbidden(*_):
            self.fail("Out-of-contract helper must never be invoked")
        result = f.evaluate_case(case(), {**surface(), "visibility": "private"},
                                 forbidden, forbidden, {}, compare_hook=forbidden)
        self.assertEqual(result["status"], "out_of_contract")
        self.assertIsNone(result["candidate_verdict"])

    def test_public_invalid_state_precondition_rejects_before_observation(self):
        result = f.evaluate_case(case(), surface(), lambda *_: {"valid": False, "reason": "invalid AST"},
                                 lambda *_: self.fail("invalid state executed"), {})
        self.assertFalse(result["admitted"])

    def test_no_blanket_error_text_normalization(self):
        def observed(value, arm, index):
            return observation(value, arm, {"kind": "error", "class": "ParseError",
                                            "message": "same" if arm == "baseline" else "changed"})
        result = self.run_case(observed)
        self.assertEqual(result["findings"][0]["kind"], "contract_regression")
        self.assertEqual(len(result["observations"]["candidate"]), 2)

    def test_exact_comparison_preserves_nested_scalar_types_and_signed_zero(self):
        for a, b in ((True, 1), (False, 0), (1, 1.0), (0.0, -0.0)):
            def observed(value, arm, index):
                return observation(value, arm, {"kind": "ok", "value": [{"nested": a if arm == "baseline" else b}]})
            self.assertEqual(self.run_case(observed)["findings"][0]["kind"], "contract_regression")
        self.assertFalse(f.typed_equal({True: "value"}, {1: "value"}))
        self.assertTrue(f.typed_equal({"a": [1, True, 1.0]}, {"a": [1, True, 1.0]}))

    def test_reviewed_numeric_comparator_is_not_overridden_by_exact_policy(self):
        def observed(value, arm, index):
            return observation(value, arm, {"kind": "ok", "value": 1 if arm == "baseline" else 1.0})
        def numeric(a, b, value):
            same = a["outcome"]["value"] == b["outcome"]["value"]
            return {"equal": same, "policy_id": "fixture-reviewed-numeric", "differences": [] if same else ["value"]}
        self.assertEqual(self.run_case(observed, compare_hook=numeric)["findings"], [])

    def test_missing_error_contract_fields_rejected_before_oracle(self):
        for payload in ({"kind": "error"}, {"kind": "error", "class": "", "message": "x"},
                        {"kind": "error", "class": 1, "message": "x"},
                        {"kind": "error", "class": "ParseError"}):
            def observed(value, arm, index):
                return observation(value, arm, payload)
            def forbidden(*_):
                self.fail("Incomplete errors must be rejected before comparator")
            with self.assertRaisesRegex(f.ContractError, "contractual error field"):
                self.run_case(observed, compare_hook=forbidden)
        f.validate_error_fields({"category": "Io", "code": 2, "io_kind": None},
                               {"category": "nonempty_string", "code": "integer",
                                "io_kind": ["nonempty_string", "null"]})
        with self.assertRaises(f.ContractError):
            f.validate_error_fields({"code": True}, {"code": "integer"})

    def test_diagnostics_not_semantics_but_accepted_bytes_are(self):
        def observed(value, arm, index):
            result = observation(value, arm)
            result["diagnostics"]["chunk_count"] = index
            return result
        self.assertEqual(self.run_case(observed)["findings"], [])
        def broken(value, arm, index):
            result = observed(value, arm, index)
            if arm == "candidate":
                result["effects"]["accepted_bytes_hex"] = ""
            return result
        self.assertEqual(self.run_case(broken)["findings"][0]["kind"], "contract_regression")

    def test_panics_and_signals_are_contract_observations(self):
        for kind, code in (("panic", 0), ("signal", -6), ("timeout", None)):
            def observed(value, arm, index):
                result = observation(value, arm)
                if arm == "candidate":
                    result["termination"] = {"kind": kind, "exit_code": code}
                return result
            self.assertEqual(self.run_case(observed)["findings"][0]["kind"], "contract_regression")

    def test_candidate_and_pristine_nondeterminism_are_distinct(self):
        def candidate_varies(value, arm, index):
            return observation(value, arm, {"kind": "ok", "value": index if arm == "candidate" else 0})
        kinds = {item["kind"] for item in self.run_case(candidate_varies)["findings"]}
        self.assertIn("nondeterministic", kinds)
        with self.assertRaisesRegex(f.ContractError, "Pristine.*nondeterministic"):
            self.run_case(lambda value, arm, index: observation(value, arm, {"kind": "ok", "value": index}))

    def test_bad_identity_missing_outcome_and_unavailable_never_pass(self):
        def wrong(value, arm, index):
            result = observation(value, arm)
            result["identity"]["case_sha256"] = H
            return result
        with self.assertRaisesRegex(f.ContractError, "identity mismatch"):
            self.run_case(wrong)
        def unavailable(value, arm, index):
            return observation(value, arm, {"kind": "unavailable"} if arm == "candidate" else None)
        self.assertEqual(self.run_case(unavailable)["findings"][0]["kind"], "undertested")


class AdequacyTests(unittest.TestCase):
    def test_corrected_surface_and_transitive_symbols_are_mandatory(self):
        authority = {"complete": True, "transitive_reexports": True,
                     "public_surface_ids": ["example.public", "workspace.Type.with_dict"]}
        manifest = {"surfaces": [surface()]}
        result = f.assess_surface(manifest, authority)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["workspace.Type.with_dict"])
        self.assertFalse(f.assess_surface(manifest, {**authority, "transitive_reexports": False})["complete"])
        with self.assertRaisesRegex(f.ContractError, "private/unresolved"):
            f.assess_surface({"surfaces": [{**surface(), "id": "private_helper"}]}, authority)

    def targets(self):
        return {"lines": [str(i) for i in range(100)], "branches": [str(i) for i in range(10)],
                "critical_branches": ["error", "lifecycle"]}

    def test_counts_are_derived_from_target_intersection_and_thresholds(self):
        targets = self.targets()
        receipt = {"identity": identity(), "targets_sha256": f.digest(targets),
                   "covered_lines": targets["lines"][:95] + ["made-up"] * 100,
                   "covered_branches": targets["branches"][:9],
                   "covered_critical_branches": targets["critical_branches"]}
        self.assertTrue(f.assess_coverage(receipt, identity(), targets)["complete"])
        receipt["covered_lines"] = receipt["covered_lines"][:94]
        self.assertFalse(f.assess_coverage(receipt, identity(), targets)["complete"])
        receipt["covered_lines"] = targets["lines"]
        receipt["covered_critical_branches"] = ["error"]
        self.assertFalse(f.assess_coverage(receipt, identity(), targets)["complete"])
        receipt["identity"] = {**identity(), "candidate_build_sha256": C}
        with self.assertRaisesRegex(f.ContractError, "identity"):
            f.assess_coverage(receipt, identity(), targets)

    def test_zero_denominator_needs_explicit_inventory_reason(self):
        targets = {"lines": [], "branches": [], "critical_branches": []}
        def receipt():
            return {"identity": identity(), "targets_sha256": f.digest(targets),
                    "covered_lines": [], "covered_branches": [], "covered_critical_branches": []}
        self.assertFalse(f.assess_coverage(receipt(), identity(), targets)["complete"])
        targets["zero_denominator_reason"] = "compiler-bound inventory proves nonexecutable-only changes"
        self.assertTrue(f.assess_coverage(receipt(), identity(), targets)["complete"])

    def mutation_receipt(self, task="example", kills=17):
        return {"identity": identity(task), "mutants": [
            {"id": str(i), "compiled": True, "killed": i < kills,
             "reproducer_sha256": H, "fresh_replays": 2} for i in range(20)],
                "controls": {"known": {"passed": True, "reproducer_sha256": H, "fresh_replays": 2}}}

    def test_mutation_minimum_rate_controls_and_invalid_not_kills(self):
        receipt = self.mutation_receipt()
        self.assertTrue(f.assess_mutations(receipt, identity(), ["known"])["complete"])
        self.assertFalse(f.assess_mutations(receipt, identity(), ["missing"])["complete"])
        receipt["mutants"][0]["compiled"] = False
        result = f.assess_mutations(receipt, identity(), ["known"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["compiled_non_equivalent"], 19)
        self.assertEqual(result["invalid"], ["0"])

    def test_xz_and_serde_require_ninety_percent_and_kills_must_replay(self):
        for task in ("xz-lzma2-compress", "serde-json-production-crate-index-ingestion"):
            receipt = self.mutation_receipt(task)
            self.assertFalse(f.assess_mutations(receipt, identity(task), ["known"])["complete"])
            receipt["mutants"][17]["killed"] = True
            self.assertTrue(f.assess_mutations(receipt, identity(task), ["known"])["complete"])
        receipt = self.mutation_receipt()
        receipt["mutants"][0]["fresh_replays"] = 1
        with self.assertRaisesRegex(f.ContractError, "Unreproduced"):
            f.assess_mutations(receipt, identity(), ["known"])
        receipt["mutants"][0]["equivalent"] = True
        with self.assertRaisesRegex(f.ContractError, "Unreviewed"):
            f.assess_mutations(receipt, identity(), ["known"])


class DecisionTests(unittest.TestCase):
    def complete(self):
        return dict.fromkeys(f.REQUIRED_OBLIGATIONS, True)

    def test_missing_obligation_and_partial_map_cannot_pass(self):
        for obligations in ({"surface": True}, {**self.complete(), "changed_coverage": False}):
            self.assertEqual(f.decide([], obligations)["status"], "undertested")
        self.assertTrue(f.decide([], self.complete())["passed"])
        with self.assertRaises(f.ContractError):
            f.decide([], {})

    def test_high_missing_memory_is_reporting_only(self):
        result = f.decide([], self.complete(), [{"unit": "one", "baseline": 1, "candidate": 1000000},
                                               {"unit": "two", "baseline": None, "candidate": None}])
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["peak_memory"]["verdict_impact"])
        self.assertEqual(result["peak_memory"]["pairs"][0]["candidate_baseline_ratio"], 1000000)
        self.assertIsNone(result["peak_memory"]["pairs"][1]["candidate_baseline_ratio"])
        with self.assertRaisesRegex(f.ContractError, "report-only"):
            f.decide([{"kind": "resource_exceeded", "resource": "peak_memory_bytes", "reproduced": True}],
                     self.complete(), approved_nonmemory_resources=["peak_memory_bytes"])

    def test_real_regression_is_not_hidden_by_undertested(self):
        result = f.decide([{"kind": "contract_regression", "reproduced": True}], {"surface": False})
        self.assertEqual(result["status"], "contract_regression")
        self.assertTrue(result["missing_obligations"])
        self.assertFalse(result["claimable"])
        with self.assertRaisesRegex(f.ContractError, "Unreproduced"):
            f.decide([{"kind": "sanitizer_error", "reproduced": False}], self.complete())
