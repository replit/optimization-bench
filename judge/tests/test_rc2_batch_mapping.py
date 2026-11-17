"""rc.2 correctness-map controls using the real bzip2 adapter, not native timing."""
import bz2
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import verifier
from runner import bundle, canonical_json, fingerprint

ROOT = Path(__file__).resolve().parents[2]


class BatchMappingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / "bzip2-codec"
        self.workload = self.task / "environment/workload"
        self.inputs = self.workload / "inputs"
        (self.inputs / "data").mkdir(parents=True)
        (self.task / "tests").mkdir()
        self.module = verifier.load_task(ROOT / "tasks/bzip2-codec")
        self.contents = {"one": b"first distinct original\n", "two": b"another original\n"}
        self.files, self.rows, self.expected_rows = {}, [], []
        self.products = {}
        for name, raw in self.contents.items():
            filename = name + ".bin"
            (self.inputs / "data" / filename).write_bytes(raw)
            self.files[filename] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            self.rows.append({"id": name, "input": filename, "direction": "compress", "level": 9})
            stream = bz2.compress(raw, compresslevel=9)
            self.assertEqual(bz2.decompress(stream), raw)
            status = canonical_json({"status": 0, "input_bytes": len(raw)})
            self.products[name] = (stream, status)
            size, digest = fingerprint(bundle({"result.bin": stream, "status.json": status}), "named-files-v1")
            self.expected_rows.append({"id": name, "bytes": size, "sha256": digest})
        (self.inputs / "manifest.json").write_text(json.dumps({
            "format": "optimbench-locked-cases-v1", "cases": "cases.json",
            "lock": "inputs.lock.json", "references": "references.json"}))
        (self.workload / "cases.json").write_text(json.dumps({"cases": self.rows}))
        (self.workload / "inputs.lock.json").write_text(json.dumps({"files": self.files}))
        (self.workload / "references.json").write_text(json.dumps(self.expected_rows))
        self.baseline, self.candidate = self.root / "baseline", self.root / "candidate"
        for path in (self.baseline, self.candidate):
            path.mkdir()
            (path / "artifact").write_bytes(b"same pristine fixture")
        self.settings = {"memory": "16g", "pids_limit": 512,
                         "minimum_pairs": 5, "maximum_pairs": 5, "warmups": 1,
                         "relative_ci_half_width": .02, "noise_thresholds": {},
                         "case_roles": {"public": {"one": "correctness", "two": "correctness",
                                                  "distinct-native-batch": "performance"}},
                         "performance_units": {"public": {"distinct-natural-workload":
                                                          ["distinct-native-batch"]}}}
        self.settings_path = self.task / "tests/judge.json"
        self.settings_path.write_text(json.dumps(self.settings))
        self.execution = {"effective_cores": list(range(8)), "requested_cores": list(range(8)),
                          "available_cores": list(range(8)), "pinning_took_effect": True,
                          "cpuset": "0-7", "host": {"boot_id_sha256": "fixture"}}

    def evaluate(self, corrupt_member=False):
        def replay(module, case, ctx, executor):
            module.prepare(case, ctx)  # Real adapter staging, with tiny authenticated inputs.
            members = case.data.get("members", [case.data])
            for member in members:
                name = member["id"]
                data, status = self.products[name]
                # Timed builds are fresh copies under timing/candidate/<k>.
                if corrupt_member and ctx.build.parent.name == "candidate" and "members" in case.data and name == "two":
                    data = data[:-1] + bytes([data[-1] ^ 1])
                directory = ctx.scratch / "results" if "members" in case.data else ctx.scratch
                binary = directory / (name + ".bin" if "members" in case.data else "result.bin")
                info = directory / (name + ".status.json" if "members" in case.data else "result.status.json")
                binary.write_bytes(data)
                info.write_bytes(status)
            step = SimpleNamespace(exit_code=0, stdout=b"", stderr=b"", leftovers=[],
                                   t0_ns=0, t1_ns=1_000_000_000,
                                   telemetry={"execution_protocol": "complete"})
            return module.collect(case, ctx, [step]), [step]
        with ExitStack() as stack:
            stack.enter_context(patch.object(verifier, "load_task", return_value=self.module))
            stack.enter_context(patch.object(verifier, "image_id", return_value="sha256:" + "1" * 64))
            stack.enter_context(patch.object(verifier.heldout, "check", return_value={"status": "available"}))
            stack.enter_context(patch.object(verifier.interfaces, "check", return_value={"passed": True}))
            stack.enter_context(patch.object(verifier.runtime_state, "review", return_value={
                "runtime_configuration_review": []}))
            stack.enter_context(patch("judge.score_host.probe", return_value={
                "host": {"boot_id_sha256": "fixture"}, "counters": {}}))
            stack.enter_context(patch.object(verifier.telemetry, "quality", return_value={
                "noisy": False, "missing_cpu_counters": False}))
            stack.enter_context(patch.object(verifier, "resources", return_value={
                "cpu_time_usec": 1000, "peak_memory_bytes": 1000}))
            stack.enter_context(patch.object(verifier, "DockerExecutor",
                                              return_value=SimpleNamespace(case_time_ns=1_000_000_000)))
            stack.enter_context(patch.object(verifier, "run_case", side_effect=replay))
            return verifier.evaluate(
                self.task, self.baseline, self.candidate, self.root, "fixture", "0-7",
                "fixture", plumbing=True, provisional=True, execution_host=self.execution,
                seed=1729, behavior_receipt={"differential": {"passed": True},
                                            "process_state": {"passed": True}})

    def test_real_bzip_batch_pristine_member_outputs_pass(self):
        result = self.evaluate()
        self.assertTrue(result["correct"])
        self.assertEqual(result["speedup"], 1)
        self.assertIn("distinct-natural-workload", result["cases"])

    def test_real_bzip_batch_corrupted_second_member_is_rejected(self):
        result = self.evaluate(corrupt_member=True)
        self.assertFalse(result["correct"])
        self.assertEqual(result["status"], "incorrect")
        self.assertIn("batch--two", json.dumps(result["records"]))

    def test_single_output_cases_have_byte_identical_rc1_and_rc2_measurements(self):
        original_cases, original_refs = self.module.cases, self.module.references
        self.settings.pop("performance_units")
        self.settings.pop("case_roles")
        self.settings_path.write_text(json.dumps(self.settings))
        def legacy_selector(module, case, cases, inputs):
            rows = list(verifier.references(module, inputs))
            expected = {r["path"]: (r["expect_len"], r["expect_sha"]) for r in rows}
            names = set(expected) if len(cases) == 1 else {case.id}
            return {name: expected[name] for name in names}
        with patch.object(self.module, "cases", side_effect=lambda inputs:
                          [c for c in original_cases(inputs) if c.id != "distinct-native-batch"]), \
                patch.object(self.module, "references", side_effect=lambda inputs:
                             [r for r in original_refs(inputs) if not r["path"].startswith("batch--")]):
            current = self.evaluate()
            with patch.object(verifier, "native_reference", side_effect=legacy_selector):
                previous = self.evaluate()
        self.assertEqual(canonical_json(current), canonical_json(previous))


if __name__ == "__main__":
    unittest.main()
