"""Focused public-only tests of the additive operation and acquisition contract."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TASK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TASK.parents[1] / "tools/runner"))
from runner import Case, Context, StepResult, canonical_json


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load("stream_adapter_test", TASK / "environment/workload/task.py")
acquire = load("stream_acquire_test", TASK / "environment/workload/fetch_inputs.py")
author = load("stream_author_test", TASK / "tests/differential/author_geojson.py")


class GeoJsonContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = b'{"type":"FeatureCollection","features":[]}\n'
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        (self.inputs / self.digest).write_bytes(self.body)
        self.doc = {"path": "stream-test", "sha256": self.digest, "bytes": len(self.body),
                    "batch": "stream-test", "operation": "stream-geojson",
                    "expect_len": 0, "expect_sha": "0" * 64}

    def manifest(self, docs):
        (self.inputs / "manifest.json").write_text(json.dumps({"splits": {"dev": [{"docs": docs}]}}))

    def context(self, docker=False):
        scratch = self.root / ("docker" if docker else "local")
        scratch.mkdir()
        build = self.root / "build"
        return Context(scratch, build, self.inputs, TASK / "environment/workload",
                       TASK / "environment/workload", docker=docker)

    def test_registration_preserves_distinct_native_and_streaming_cases(self):
        native = {**self.doc, "path": "native-test", "batch": "native-test"}
        del native["operation"]
        self.manifest([native, self.doc])
        cases = adapter.cases(self.inputs)
        self.assertEqual([case.id for case in cases], ["native-test", "stream-test"])
        self.assertEqual(cases[0].data["docs"], [native])

    def test_rejects_mixed_unknown_or_multiple_streaming_documents(self):
        for other in [
            {**self.doc, "path": "other", "operation": "unknown"},
            {**self.doc, "path": "other", "operation": "strip-descriptions"},
            {**self.doc, "path": "other"},
        ]:
            with self.subTest(operation=other["operation"]):
                self.manifest([self.doc, other])
                with self.assertRaises(ValueError):
                    adapter.cases(self.inputs)

    def test_target_mapping_and_exact_complete_cli(self):
        for docker in [False, True]:
            with self.subTest(docker=docker):
                ctx = self.context(docker)
                steps = adapter.prepare(Case("stream-test", {"docs": [self.doc]}), ctx)
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].argv[1:],
                                 ["-cn", "--stream", adapter.STREAM_FILTER, "input.geojson"])
                base = "/app/build" if docker else str(ctx.build)
                self.assertEqual(steps[0].env["LD_LIBRARY_PATH"],
                                 base + "/project/.libs:" + base + "/project/vendor/oniguruma/src/.libs")
                self.assertEqual((ctx.scratch / "input.geojson").read_bytes(), self.body)
                prepared = json.loads((ctx.scratch / "cases.json").read_text())
                self.assertNotIn("expect_sha", prepared[0])
                self.assertNotIn("expect_len", prepared[0])

    def test_input_changes_fail_before_execution(self):
        (self.inputs / self.digest).write_bytes(self.body.replace(b"[]", b"{}"))
        with self.assertRaisesRegex(Exception, "Input changed during preparation"):
            adapter.prepare(Case("stream-test", {"docs": [self.doc]}), self.context())

    def test_complete_partial_output_error_and_status_are_retained(self):
        case = Case("stream-test", {"docs": [self.doc]})
        ctx = self.context()
        result = StepResult(5, b'{"x":1}\n', b"parse error\n")
        observation = adapter.collect(case, ctx, [result])["stream-test"]
        self.assertEqual(observation, canonical_json({
            "exit_code": 5, "stdout_base64": "eyJ4IjoxfQo=",
            "stderr_base64": "cGFyc2UgZXJyb3IK"}))
        with self.assertRaises(ValueError):
            adapter.collect(case, ctx, [])

    def test_full_source_commitment_rejects_same_size_tail_change(self):
        sample = b"original-prefix-original-tail"
        with patch.object(author, "SOURCE_BYTES", len(sample)), patch.object(
                author, "SOURCE_SHA", hashlib.sha256(sample).hexdigest()):
            author.validate_source(sample)
            with self.assertRaisesRegex(ValueError, "Complete publisher snapshot changed"):
                author.validate_source(sample[:-1] + b"X")

    def test_public_prefix_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "Publisher snapshot prefix changed"):
            acquire.extract(self.body)

    def test_existing_authored_bytes_are_never_overwritten(self):
        path = self.root / "frozen"
        author.write_new(path, b"original")
        author.write_new(path, b"original")
        with self.assertRaisesRegex(ValueError, "Refusing to replace"):
            author.write_new(path, b"changed")
        self.assertEqual(path.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
