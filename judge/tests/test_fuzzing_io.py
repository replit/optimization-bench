"""Policy controls, not a claim of task qualification."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from judge import fuzzing_io as io


def stream(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "head_base64": base64.b64encode(data).decode(), "head_truncated": False}


class SharedIOPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = self.root / "example"
        (self.task / "tests/fuzz").mkdir(parents=True)
        (self.task / "tests/judge.json").write_text("{}")
        self.surface = {"id": "cli", "visibility": "public", "contract_id": "cli-contract",
                        "precondition_id": "valid"}
        self.case = {"task": "example", "surface_id": "cli", "contract_id": "cli-contract",
                     "precondition_id": "valid", "input": {}, "options": {}, "sequence": [], "faults": []}
        self.specs = [{"case": {**self.case, "input": {"size": size}}, "surface": self.surface,
                       "step": {"argv": ["/app/build/tool", "input"]}, "inputs": self.root,
                       "watch_inputs": ["input"], "payload_class": size}
                      for size in ("small", "large")]
        self.request = {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": self.specs,
                        "readonly_applicability_evidence": {"fixture_contract": "stdout-only test toy"}}
        self.hooks = SimpleNamespace(CLI_CASES=lambda _: self.request,
                                     PRECONDITION=lambda *_: {"valid": True, "reason": "valid IO input"})
        self.context = {"task": self.task, "baseline": self.root/"baseline", "candidate": self.root/"candidate",
                        "image": "image", "out": self.root/"out", "cpuset": "0",
                        "public_inputs": None, "projects": None, "hooks": self.hooks,
                        "builds": {"baseline": "a"*64, "candidate": "b"*64},
                        "sources": {"baseline": "c"*64, "candidate": "d"*64}}

    def pair(self, **kwargs):
        healthy = b"x"*16 if kwargs["payload_class"] == "small" else b"x"*65536
        facts = {}
        for fault in kwargs["faults"]:
            row = {"status": "observed", "exit_code": 0 if fault == "healthy" else 1, "signal": None,
                   "stdout": stream(healthy if fault == "healthy" else b""),
                   "stderr": stream(b"" if fault == "healthy" else b"write failed\n"),
                   "stdout_capture_available": fault == "healthy",
                   "input_before": {"input": "old"}, "input_after": {"input": "old"},
                   "output_after": {}, "required_outputs": {}, "stopped_descendants": []}
            facts[fault] = {arm: copy.deepcopy(row) for arm in ("baseline", "candidate")}
        return {"observations": facts}

    def run_cli(self, pair=None):
        with patch("tools.fuzz_runners.cli_faults.run_pair", side_effect=pair or self.pair):
            return io.cli_gate(self.context)

    def test_real_requests_not_self_awarded_status_and_two_payload_classes_required(self):
        self.request["status"] = "passed"
        self.assertEqual(self.run_cli()["status"], "passed")
        self.specs.pop()
        self.assertEqual(self.run_cli()["status"], "pending")

    def test_swallowed_flush_error_and_file_rewrite_reproduced(self):
        for change in ("exit", "input"):
            def changed(**kwargs):
                raw = self.pair(**kwargs)
                candidate = raw["observations"]["dev_full"]["candidate"]
                if change == "exit":
                    candidate["exit_code"] = 0
                else:
                    candidate["input_after"] = {"input": "rewritten"}
                return raw
            self.assertEqual(self.run_cli(changed)["status"], "contract_regression")

    def test_empty_stderr_is_preserved_and_no_universal_failure_exit_is_invented(self):
        def silent(**kwargs):
            raw = self.pair(**kwargs)
            for fault, pair in raw["observations"].items():
                if fault != "healthy":
                    for row in pair.values():
                        row["exit_code"], row["stderr"] = 0, stream(b"")
            return raw
        result = self.run_cli(silent)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["evidence"]["baseline_limitations"])

    def test_declared_json_self_repeatability_preflight_not_hardcoded_byte_hash(self):
        calls = [0]
        def reordered(**kwargs):
            raw = self.pair(**kwargs)
            calls[0] += 1
            padding = "x" * (65536 if kwargs["payload_class"] == "large" else 1)
            value = {"a": 1, "padding": padding} if calls[0] % 2 else {"padding": padding, "a": 1}
            for row in raw["observations"]["healthy"].values():
                row["stdout"] = stream(json.dumps(value).encode())
            return raw
        with self.assertRaises(io.BaselineNonrepeatable):
            self.run_cli(reordered)
        from judge.output_contract import DEFAULT
        policy = copy.deepcopy(DEFAULT)
        policy["stdout"] = {"kind": "json-structural"}
        (self.task / "tests/judge.json").write_text(json.dumps({"output_comparator": policy}))
        calls[0] = 0
        result = self.run_cli(reordered)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["evidence"]["output_comparator"], policy)

    def test_private_cli_never_invokes_worker(self):
        self.surface["visibility"] = "private"
        with patch("tools.fuzz_runners.cli_faults.run_pair") as worker:
            self.assertEqual(io.cli_gate(self.context)["status"], "pending")
            worker.assert_not_called()

    def test_meaningless_readonly_directory_does_not_earn_credit(self):
        self.specs[0].update(output_dirs=["out"], required_outputs=["out/result"])
        with self.assertRaisesRegex(ValueError, "actual healthy file"):
            self.run_cli()

    def test_permission_setup_only_cannot_prove_readonly_write_coverage(self):
        file = {"kind": "file", "bytes": 4, "sha256": "a"*64, "mode": 420}
        self.specs[0].update(output_dirs=["."], required_outputs=["input"])
        def unchanged(**kwargs):
            raw = self.pair(**kwargs)
            for fault, pair in raw["observations"].items():
                for row in pair.values():
                    row["input_before"] = {"input": {"input": dict(file)}}
                    row["input_after"] = {"input": {"input": dict(file)}}
                    row["required_outputs"] = {"input": {"input": dict(file)}}
                    if fault == "readonly_dir":
                        row["exit_code"] = 0
                        row["required_outputs"]["input"]["input"]["mode"] = 292
                        row["input_after"]["input"]["input"]["mode"] = 292
            return raw
        with self.assertRaisesRegex(ValueError, "healthy content write"):
            self.run_cli(unchanged)

    def test_truncated_preview_is_never_treated_as_complete_stderr(self):
        value = stream(b"Traceback")
        value["head_truncated"] = True
        with self.assertRaisesRegex(ValueError, "truncated"):
            io.stream_bytes(value, self.root)
        path = self.root / "stderr.bin"
        data = b"x"*40000+b"\ninput.sql:17:9\n"
        path.write_bytes(data)
        value.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     artifact={"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "max_bytes": 65536, "complete": True})
        self.assertEqual(io.stream_bytes(value, self.root), data)
        value["artifact"]["sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "commitment"):
            io.stream_bytes(value, self.root)

    def test_headers_use_two_fresh_pairs_and_baseline_failure_is_not_candidate_regression(self):
        manifest = self.task / "tests/fuzz/headers.json"
        manifest.write_text("{}")
        self.hooks.HEADER_CONSUMERS = lambda _: {
            "format": "judge-generic-header-request-v1", "applicable": True, "manifest": manifest}
        for attribution, verdict in (("same_success", "passed"),
                                      ("candidate_compatibility_difference", "contract_regression"),
                                      ("baseline_supported_cell_failed", "invalid_run"),
                                      ("missing_third_party_sdk", "not_applicable"),
                                      ("support_evidence_pending", "pending")):
            with patch("tools.fuzz_runners.header_run.run_pair", return_value={
                    "cells": [{"attribution": attribution}]}) as worker:
                self.assertEqual(io.header_gate(self.context)["status"], verdict)
                self.assertEqual(worker.call_count, 2)

        for scope in ({"status": "unresolved"}, {"status": "partially_established"},
                      {"status": "established", "unmapped_contexts": ["advertised.h"]}):
            manifest.write_text(json.dumps({"cxx_scope": scope}))
            with patch("tools.fuzz_runners.header_run.run_pair", return_value={
                    "cells": [{"attribution": "same_success"}]}):
                self.assertEqual(io.header_gate(self.context)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
