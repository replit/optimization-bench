import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Context, HarnessError, StepResult

WORKLOAD = ROOT / "tasks/sqlfmt-cli/environment/workload"
spec = importlib.util.spec_from_file_location("native_sqlfmt_task", WORKLOAD / "task.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class NativeSQLFmtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        (self.inputs / "files").mkdir(parents=True)
        (self.inputs / "manifest.json").write_text('{"splits": {}}')
        data = b"select 1;\n"
        digest = hashlib.sha256(data).hexdigest()
        (self.inputs / "files" / digest).write_bytes(data)
        self.manifest = {
            "format": "sqlfmt-native-batch-v1",
            "docs": [{"id": "query", "sha256": digest, "bytes": len(data)}],
            "expected": {"expect_len": 3, "expect_sha": hashlib.sha256(b"SQL").hexdigest()},
        }
        self.save()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.build = self.root / "build"
        self.build.mkdir()
        self.ctx = Context(self.scratch, self.build, self.inputs, self.root, WORKLOAD)

    def save(self):
        (self.inputs / "native-batch.json").write_text(json.dumps(self.manifest))

    def test_original_and_native_units_are_both_enumerated(self):
        cases = adapter.cases(self.inputs)
        self.assertEqual([c.id for c in cases], ["batch", "native-batch"])
        self.assertEqual(cases[0].data, {"inputs": [], "docs": []})

    def test_native_manifest_cannot_silently_disappear(self):
        (self.inputs / "native-batch.json").unlink()
        with self.assertRaisesRegex(HarnessError, "requires"):
            adapter.cases(self.inputs)

    def test_missing_or_corrupt_native_input_is_rejected(self):
        digest = self.manifest["docs"][0]["sha256"]
        (self.inputs / "files" / digest).write_bytes(b"wrong")
        with self.assertRaisesRegex(HarnessError, "checksum"):
            adapter.cases(self.inputs)

    def test_duplicate_identity_or_body_is_rejected(self):
        self.manifest["docs"].append(dict(self.manifest["docs"][0]))
        self.save()
        with self.assertRaises(HarnessError):
            adapter.cases(self.inputs)
        self.manifest["docs"][1]["id"] = "different"
        self.save()
        with self.assertRaisesRegex(HarnessError, "Duplicate"):
            adapter.cases(self.inputs)

    def test_path_escape_is_rejected(self):
        self.manifest["docs"][0]["sha256"] = "../escape"
        self.save()
        with self.assertRaises(HarnessError):
            adapter.cases(self.inputs)

    def test_prepare_is_one_whole_process_with_complete_request(self):
        case = adapter.cases(self.inputs)[1]
        steps = adapter.prepare(case, self.ctx)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].argv, [str(self.build / "sqlfmt-native")])
        self.assertFalse(steps[0].resident)
        request = json.loads((self.scratch / steps[0].stdin_file).read_bytes())
        self.assertEqual(request[0]["id"], "query")
        self.assertEqual(base64.b64decode(request[0]["sql"]), b"select 1;\n")
        self.assertEqual(list(self.scratch.iterdir()), [self.scratch / "native-request.json"])

    def test_collect_preserves_full_result_status_and_stderr(self):
        case = adapter.cases(self.inputs)[1]
        result = StepResult(7, b'[{"output":"partial"}]\n', b"failure\n")
        actual = adapter.collect(case, self.ctx, [result])
        self.assertEqual(actual["native-batch.stdout"],
                         b'EXIT 7\nSTDOUT - 23\n[{"output":"partial"}]\n')
        self.assertEqual(actual["native-batch.stderr"], b"failure\n")
        self.assertNotEqual(actual, adapter.collect(case, self.ctx, [StepResult(0, b"[]", b"")]))

    def test_missing_step_result_is_rejected(self):
        with self.assertRaisesRegex(HarnessError, "Missing"):
            adapter.collect(adapter.cases(self.inputs)[1], self.ctx, [])

    def test_native_reference_has_both_complete_stdout_and_stderr(self):
        references = adapter.references(self.inputs)
        self.assertEqual({r["path"] for r in references}, {"native-batch.stdout", "native-batch.stderr"})
        self.assertEqual(references[0]["expect_sha"], self.manifest["expected"]["expect_sha"])

    def test_shared_verifier_scopes_every_stdout_and_stderr_to_its_case(self):
        from judge.process_state import native_reference
        raw = b"CLI1\nsqlfmt\nstdin\n-\nselect 1;\n"
        digest = hashlib.sha256(raw).hexdigest()
        (self.inputs / "files" / digest).write_bytes(raw)
        original = {"sha256": digest, "size": len(raw), "expect_len": 3,
                    "expect_sha": hashlib.sha256(b"SQL").hexdigest()}
        (self.inputs / "manifest.json").write_text(json.dumps({
            "splits": {"train": [{"docs": [original]}]}}))
        cases = adapter.cases(self.inputs)
        scopes = [native_reference(adapter, case, cases, self.inputs) for case in cases]
        self.assertEqual(set(scopes[0]), {f"input-0000-{digest[:12]}",
                                         f"input-0000-{digest[:12]}.stderr"})
        self.assertEqual(set(scopes[1]), {"native-batch.stdout", "native-batch.stderr"})
        self.assertFalse(set(scopes[0]) & set(scopes[1]))
        self.assertEqual(set(scopes[0]) | set(scopes[1]),
                         {row["path"] for row in adapter.references(self.inputs)})

    def test_shared_runner_and_scope_are_unchanged(self):
        self.assertEqual((WORKLOAD / "runner.py").read_bytes(), (ROOT / "tools/runner/runner.py").read_bytes())
        source = json.loads((WORKLOAD.parents[1] / "release.json").read_text())["source_policy"]
        self.assertEqual(source["source_roots"], ["format", "cmd/sqlfmt"])
        self.assertIn("cmd/sqlfmt/explaincmd.go", source["protected_paths"])


if __name__ == "__main__":
    unittest.main()
