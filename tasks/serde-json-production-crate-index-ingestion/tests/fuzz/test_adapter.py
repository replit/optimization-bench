import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.cases = list(adapter.generate(random.Random(1729), {"cases": 120}))
        self.surface = json.loads((HERE / "surface.json").read_text())

    def observation(self, case):
        return {"format": "optimbench-fuzz-observation-v1",
                "identity": {"task": adapter.TASK, "surface_id": case["surface_id"],
                             "case_sha256": adapter.case_sha256(case), "build_sha256": "a"*64},
                "termination": {"kind": "return", "exit_code": 0},
                "outcome": {"kind": "ok", "value": None},
                "effects": {"accepted_bytes_hex": "6162"},
                "diagnostics": {"calls": 1, "execution_affinity": [0]}}

    def test_generated_domains_and_transport(self):
        self.assertEqual({c["surface_id"] for c in self.cases}, set(adapter.SURFACES))
        for case in self.cases:
            self.assertTrue(adapter.precondition(case, self.surface)["valid"], case)
            self.assertTrue(adapter.encode(case).startswith(b"SJF1"))

    def test_seed_replay(self):
        self.assertEqual(self.cases, list(adapter.generate(random.Random(1729), {"cases": 120})))

    def test_fault_preserving_shrink(self):
        for case in self.cases:
            for small in adapter.shrink(case):
                self.assertTrue(adapter.precondition(small, self.surface)["valid"])
                self.assertEqual(small["surface_id"], case["surface_id"])
                self.assertEqual(small["faults"]["mode"], case["faults"]["mode"])
                self.assertEqual(small["options"], case["options"])

    def test_no_candidate_parser_or_serializer_in_transport(self):
        self.assertEqual(adapter.typed({"x": [None, True, 42]}),
                         b"o\x01\0\0\0s\x01\0\0\0xa\x03\0\0\0nti" + (42).to_bytes(8, "little"))

    def test_invalid_private_or_undefined_states(self):
        for change in ({"surface_id": "serde_json::private_helper"},
                       {"sequence": ["reset-invalid-state"]},
                       {"options": {"feature_profile": "unsupported", "serialize_then_panic": False}},
                       {"faults": {"mode": "short", "max_chunk": 0, "threshold": 0, "error_kind": "Other"}}):
            bad = {**self.cases[0], **change}
            self.assertFalse(adapter.precondition(bad, self.surface)["valid"])

    def test_from_str_requires_utf8(self):
        case = copy.deepcopy(self.cases[5])
        case["input"] = {"bytes_hex": "ff"}
        self.assertFalse(adapter.precondition(case, self.surface)["valid"])

    def test_chunking_must_not_flag(self):
        left = self.observation(self.cases[0])
        right = copy.deepcopy(left)
        right["identity"]["build_sha256"] = "b"*64
        right["diagnostics"] = {"calls": 100, "chunking": [1, 1]}
        self.assertTrue(adapter.compare(left, right, self.cases[0])["equal"])

    def test_error_panic_partial_output_must_flag(self):
        left = self.observation(self.cases[0])
        for key, value in (("termination", {"kind": "panic"}),
                           ("outcome", {"kind": "error", "io_kind": "BrokenPipe"}),
                           ("effects", {"accepted_bytes_hex": "61"})):
            right = {**left, key: value}
            self.assertFalse(adapter.compare(left, right, self.cases[0])["equal"])

    def test_malformed_transport_cannot_equal_itself(self):
        for bad in ({}, {"format": "wrong"}, self.observation(self.cases[1])):
            with self.assertRaises(ValueError):
                adapter.compare(bad, bad, self.cases[0])

    def test_typed_scalar_differences_cannot_cancel(self):
        left = self.observation(self.cases[0])
        right = copy.deepcopy(left)
        left["outcome"]["value"] = True
        right["outcome"]["value"] = 1
        self.assertFalse(adapter.compare(left, right, self.cases[0])["equal"])

    def test_shared_contract_entry_precondition(self):
        case = self.cases[0]
        self.assertTrue(adapter.precondition(case, {"id": case["surface_id"]})["valid"])

    def test_depth_bound_before_native_execution(self):
        value = None
        for _ in range(70):
            value = [value]
        case = copy.deepcopy(self.cases[0])
        case["input"]["value"] = value
        self.assertFalse(adapter.precondition(case, self.surface)["valid"])

    def test_canonical_registration_inventory(self):
        repository = HERE.parents[3]
        sys.path.insert(0, str(repository))
        from judge.fuzzing_generic import task_runners
        callbacks, module = task_runners(HERE.parents[1])
        self.assertEqual(set(callbacks), {"stream_faults","repeat_in_process","aliasing"})
        self.assertTrue(callable(module.CLI_CASES))
        self.assertFalse(module.HEADER_CONSUMERS({})["applicable"])
        self.assertTrue(module.HEADER_CONSUMERS({})["applicability_evidence"])

    def test_driver_exclusion_requires_covering_callback_registration(self):
        from judge.fuzzing_generic import task_runners
        from judge.fuzzing_io import inapplicable
        _, module = task_runners(HERE.parents[1])
        module.GATES.pop("stream_faults")
        result = module.CLI_CASES({})
        self.assertFalse(result["applicability_evidence"])
        self.assertEqual(result["coverage_status"], "unmeasured")
        self.assertEqual(inapplicable(result)["status"], "pending")

    def test_driver_exclusion_requires_complete_bound_library_facts(self):
        from judge.fuzzing_generic import task_runners
        _, module = task_runners(HERE.parents[1])
        cases = self.cases[:8]
        surfaces = {
            "visibility":"public","contract_id":adapter.POLICY,
            "precondition_id":"public-typed-json-io-v1",
            "oracle":{"required_error_fields":{"category":"nonempty_string"}},
        }
        rows = [{"case":case, "surface":{**surfaces,"id":case["surface_id"]},
                 "baseline":[self.observation(case)]*2,
                 "candidate":[self.observation(case)]*2} for case in cases]
        facts = {
            "applicable":True,"cases":rows,
            "workers":{arm:{"mode":"persistent","target_library_in_process":True,"calls":16}
                       for arm in ("baseline","candidate")},
            "lifecycle":[{arm:{"available":True} for arm in ("baseline","candidate")}],
        }
        module._facts = lambda *_: facts
        context = {"builds":{"baseline":"a"*64,"candidate":"a"*64}}
        good = module.CLI_CASES(context)
        self.assertFalse(good["applicable"])
        self.assertTrue(good["applicability_evidence"]["facts_sha256"])
        facts["cases"] = rows[:-1]
        missing = module.CLI_CASES(context)
        self.assertFalse(missing["applicability_evidence"])
        self.assertEqual(missing["coverage_status"], "unmeasured")

    def test_aliasing_cases_have_real_native_operation_and_bounded_domain(self):
        cases = list(adapter.generate_aliases(random.Random(2718)))
        self.assertEqual(len(cases), 2)
        self.assertNotEqual(cases[0], cases[1])
        for case in cases:
            self.assertTrue(adapter.precondition(case, self.surface)["valid"])
            self.assertEqual(adapter.encode(case)[15], 2)
            for smaller in adapter.shrink(case):
                self.assertTrue(adapter.precondition(smaller, self.surface)["valid"])
            bad = copy.deepcopy(case)
            bad["input"] = {"bytes_hex": b"null".hex()}
            self.assertFalse(adapter.precondition(bad, self.surface)["valid"])
            bad = copy.deepcopy(case)
            bad["surface_id"] = "serde_json::from_reader"
            self.assertFalse(adapter.precondition(bad, self.surface)["valid"])

    def test_aliasing_compares_complete_retained_values_and_input(self):
        case = next(adapter.generate_aliases(random.Random(2718)))
        left = self.observation(case)
        left["effects"] = {"retained_tape_hex": "6e", "independent_tape_hex": "6e",
                           "mutated_tape_hex": "74", "shared_input_hex": "7b7d",
                           "borrowed_entries": [{"key_hex": "61", "value_tape_hex": "6e"}]}
        for field in left["effects"]:
            right = copy.deepcopy(left)
            right["effects"][field] = [] if field == "borrowed_entries" else "00"
            self.assertFalse(adapter.compare(left, right, case)["equal"])


class ImageTransportTests(unittest.TestCase):
    def test_independent_acquisition_guard_remains_frozen(self):
        guard = HERE.parents[1] / "environment/payload_check.py"
        self.assertEqual(hashlib.sha256(guard.read_bytes()).hexdigest(),
                         "c471a0e41c35fc0c0ec609489d3de301130ae60203b9dab6d270aa345e22ee24")

    def test_verifier_compilers_remain_pinned_worker_dependencies(self):
        from tools import judge_command_audit
        task, root = HERE.parents[1], HERE.parents[3]
        for relative in (
            "tests/differential/run.py",
            "tests/differential/validate_writer_reference.py",
            "tests/fuzz/run.py",
            "tests/fuzz/validate_aliasing.py",
            "tests/upstream.py",
        ):
            with self.subTest(relative=relative):
                rows = judge_command_audit.inspect_source(task / relative, root)
                self.assertTrue(rows)
                self.assertTrue(all(row["execution_context"] in (
                    "pinned-worker", "pinned-worker-verifier-observer") for row in rows))

    def test_runtime_packet_exposes_only_the_original_input_root(self):
        packet = HERE.parents[1] / "environment/public-input-packet.json"
        self.assertEqual(json.loads(packet.read_text()), {
            "schema": "optimbench-public-input-packet-v1",
            "mounts": ["inputs/files"],
        })

    def test_original_inputs_are_authenticated_in_external_build_cache(self):
        environment = HERE.parents[1] / "environment"
        manifest = environment / "workload/inputs/manifest.json"
        cache = "optimbench-serde-json-public-" + hashlib.sha256(manifest.read_bytes()).hexdigest()
        dockerfile = (environment / "Dockerfile").read_text()
        self.assertIn(f"id={cache},target=/payload/workload/inputs/files,sharing=locked", dockerfile)
        self.assertIn(f"id={cache},target=/app/workload/inputs/files,sharing=locked,readonly", dockerfile)
        self.assertIn("python3 /payload/payload_check.py --fetch /payload", dockerfile)
        self.assertIn("python3 /app/workload/check_public.py /app/baseline-build/prog", dockerfile)

    def test_staged_input_bodies_cannot_enter_the_build_context(self):
        rules = (HERE.parents[1] / "environment/.dockerignore").read_text().splitlines()
        self.assertIn("workload/inputs/**", rules)
        self.assertGreater(rules.index("!workload/inputs/manifest.json"), rules.index("workload/inputs/**"))

    def test_runtime_workload_does_not_copy_the_acquisition_stage(self):
        dockerfile = (HERE.parents[1] / "environment/Dockerfile").read_text()
        self.assertIn("COPY workload/ /app/workload/", dockerfile)
        self.assertNotIn("COPY --from=payload-guard /payload/workload/", dockerfile)


if __name__ == "__main__":
    unittest.main()
