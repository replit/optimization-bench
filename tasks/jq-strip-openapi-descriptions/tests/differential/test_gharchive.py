"""Small fixtures only. Never acquire or inspect protected corpus members."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch

TASK = Path(__file__).resolve().parents[2]
ROOT = TASK.parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Case, Context, StepResult, canonical_json

spec = importlib.util.spec_from_file_location("gharchive_adapter", TASK / "environment/workload/task.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
aa_spec = importlib.util.spec_from_file_location("gharchive_aa", Path(__file__).with_name("run_aa.py"))
aa = importlib.util.module_from_spec(aa_spec)
aa_spec.loader.exec_module(aa)


class GhArchiveTests(unittest.TestCase):
    def test_release_has_four_equally_weighted_units_in_both_splits(self):
        from judge.verifier import case_roles, performance_units
        settings = json.loads((TASK / "tests/judge.json").read_text())
        for public, relative, counts in (
            (True, "environment/workload/inputs", [30, 1, 1, 1]),
            (False, "tests/heldout", [9, 1, 1, 1]),
        ):
            inputs = TASK / relative
            manifest = json.loads((inputs / "manifest.json").read_text())
            docs = [doc for batches in manifest["splits"].values()
                    for batch in batches for doc in batch["docs"]]
            with self.subTest(public=public), patch.object(adapter, "documents", return_value=docs):
                cases = adapter.cases(inputs)
                self.assertEqual([len(case.data["docs"]) for case in cases], counts)
                self.assertEqual([case.data["docs"][0].get("operation", "strip-descriptions")
                                  for case in cases],
                                 ["strip-descriptions", "stream-geojson",
                                  "group-gharchive", "csv-gharchive"])
                roles = case_roles(settings, {case.id: case for case in cases}, public)
                units = performance_units(settings, roles, public)
                self.assertEqual(units, None if public else
                                 {case.id: [case.id] for case in cases})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs, self.scratch = self.root / "inputs", self.root / "scratch"
        self.inputs.mkdir()
        self.scratch.mkdir()
        self.raw = b'{"id":"1","repo":{"name":"r"},"type":"PushEvent"}\n'
        self.archive = gzip.compress(self.raw, mtime=0)
        sha = hashlib.sha256(self.archive).hexdigest()
        (self.inputs / sha).write_bytes(self.archive)
        self.doc = {"path": "fixture", "batch": "fixture", "operation": "group-gharchive",
                    "sha256": sha, "bytes": len(self.archive),
                    "decoded_bytes": len(self.raw),
                    "decoded_sha256": hashlib.sha256(self.raw).hexdigest()}

    def context(self):
        return Context(self.scratch, self.root / "build", self.inputs,
                       self.inputs, TASK / "environment/workload", docker=True)

    def test_two_distinct_units_use_one_original_hour(self):
        docs = [{**self.doc, "path": op, "batch": op, "operation": op}
                for op in ("group-gharchive", "csv-gharchive")]
        (self.inputs / "manifest.json").write_text(
            json.dumps({"splits": {"dev": [{"docs": docs}]}}))
        cases = adapter.cases(self.inputs)
        self.assertEqual(len(cases), 2)
        self.assertNotEqual(cases[0].id, cases[1].id)
        self.assertTrue(all(len(case.data["docs"]) == 1 for case in cases))
        self.assertEqual(docs[0]["sha256"], docs[1]["sha256"])

    def test_exact_cli_and_decoding_before_execution(self):
        for op, options in [("group-gharchive", ["-nc", adapter.GROUP_FILTER]),
                            ("csv-gharchive", ["-r", adapter.CSV_FILTER])]:
            doc = {**self.doc, "operation": op}
            steps = adapter.prepare(Case("fixture", {"docs": [doc]}), self.context())
            self.assertEqual(steps[0].argv[1:], [*options, "events.jsonl"])
            self.assertEqual((self.scratch / "events.jsonl").read_bytes(), self.raw)
            self.assertEqual(len(steps), 1)
            self.assertNotIn("gzip", " ".join(steps[0].argv))

    def test_decoded_size_and_full_tail_hash_are_checked(self):
        for changed in [
            {"decoded_bytes": len(self.raw) - 1},
            {"decoded_bytes": len(self.raw) + 1},
            {"decoded_sha256": hashlib.sha256(self.raw[:-1] + b"X").hexdigest()},
        ]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                adapter.prepare(Case("fixture", {"docs": [{**self.doc, **changed}]}),
                                self.context())

    def test_transport_changes_are_rejected_before_decoding(self):
        path = self.inputs / self.doc["sha256"]
        path.write_bytes(self.archive[:-1] + bytes([self.archive[-1] ^ 1]))
        with self.assertRaisesRegex(Exception, "Input changed during preparation"):
            adapter.prepare(Case("fixture", {"docs": [self.doc]}), self.context())

    def test_no_multi_document_or_mixed_operation_unit(self):
        for other in ({**self.doc, "path": "second"},
                      {**self.doc, "path": "second", "operation": "csv-gharchive"}):
            (self.inputs / "manifest.json").write_text(json.dumps({
                "splits": {"dev": [{"docs": [self.doc, other]}]}}))
            with self.assertRaises(ValueError):
                adapter.cases(self.inputs)

    def test_complete_status_stderr_and_partial_output_are_preserved(self):
        case = Case("fixture", {"docs": [self.doc]})
        result = StepResult(5, b"partial\n", b"error\n")
        actual = adapter.collect(case, self.context(), [result])["fixture"]
        self.assertEqual(actual, canonical_json(result.streams()))
        for bad in (StepResult(0, b"", b""), StepResult(0, b"wrong", b""),
                    StepResult(0, result.stdout, b""), StepResult(0, result.stdout, result.stderr)):
            self.assertNotEqual(adapter.collect(case, self.context(), [bad])["fixture"], actual)

    def test_aa_wrapper_forwards_options_and_acquires_only_private_inputs(self):
        for scope in ("public", "heldout"):
            args = ["run_aa.py", "--task", "/original", "--scope", scope,
                    "--image", "fixture-image", "--minimum", "5", "--maximum", "30",
                    "--seed", "1729", "--output", "/operator/result.json"]
            with patch.object(sys, "argv", args), patch.object(
                    aa.heldout, "materialized_task", return_value=nullcontext(Path("/external"))) as fetch:
                seen = []
                with patch.object(aa.qualify_aa, "main", side_effect=lambda: seen.append(sys.argv[:]) or 0):
                    self.assertEqual(aa.main(), 0)
                self.assertEqual(seen[0][5:], args[5:])
                self.assertEqual(seen[0][2], "/external" if scope == "heldout" else "/original")
                self.assertEqual(fetch.call_count, int(scope == "heldout"))


if __name__ == "__main__":
    unittest.main()
