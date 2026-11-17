"""Focused correctness-only tests; never execute a timing path."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT/"tools/runner"))
spec = importlib.util.spec_from_file_location(
    "serde_realism_task",Path(__file__).resolve().parents[3]/"environment/workload/task.py")
task = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task)
corpus_spec = importlib.util.spec_from_file_location(
    "serde_realism_corpus",Path(__file__).with_name("build_corpus.py"))
corpus = importlib.util.module_from_spec(corpus_spec)
corpus_spec.loader.exec_module(corpus)


class ResidentAdapterTests(unittest.TestCase):
    def inputs(self, root, repeat=False):
        (root/"files").mkdir()
        docs = []
        for name,raw in (("one",b'{"name":"one"}'),("two",b'{"name":"two"}')):
            digest = hashlib.sha256(raw).hexdigest()
            (root/"files"/digest).write_bytes(raw)
            docs.append({"name":name,"path":name,"sha256":digest,"bytes":len(raw),
                         "expect_len":len(raw),"expect_sha":digest})
        if repeat:
            docs[1] = {**docs[0],"path":"two"}
        (root/"manifest.json").write_text(json.dumps({"splits":{"public":[{"docs":docs}]}}))
        return docs

    def test_one_resident_step_and_one_reference_per_distinct_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = self.inputs(root)
            scratch = root/"scratch"
            scratch.mkdir()
            case, = task.cases(root)
            ctx = SimpleNamespace(inputs=root,scratch=scratch,build=Path("/app/build"),
                                  target=lambda value:str(value))
            steps = task.prepare(case,ctx)
            self.assertEqual(len(steps),1)
            self.assertTrue(steps[0].resident)
            self.assertIn("--resident-batch",steps[0].argv)
            self.assertEqual(len((scratch/"batch-list.tsv").read_text().splitlines()),2)
            self.assertEqual([d["path"] for d in task.references(root)],["one","two"])

    def test_no_repeated_content_in_one_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.inputs(root,repeat=True)
            with self.assertRaises(task.HarnessError):
                task.cases(root)

    def test_collect_requires_every_full_result_and_no_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = self.inputs(root)
            case, = task.cases(root)
            scratch = root/"scratch"
            results = scratch/"results"
            results.mkdir(parents=True)
            for doc in docs:
                (results/(doc["path"]+".json")).write_bytes(
                    (root/"files"/doc["sha256"]).read_bytes())
            completed = SimpleNamespace(exit_code=0,stdout=b"",stderr=b"",leftovers=[],
                telemetry={"execution_protocol":"complete"},
                streams=lambda:{"exit_code":0,"stdout_base64":"","stderr_base64":""})
            ctx = SimpleNamespace(scratch=scratch)
            outputs = task.collect(case,ctx,[completed])
            self.assertEqual(set(outputs),{"one","two"})
            for doc in docs:
                self.assertEqual(hashlib.sha256(outputs[doc["path"]]).hexdigest(),doc["expect_sha"])
            (results/"extra.json").write_text("{}")
            for value in task.collect(case,ctx,[completed]).values():
                self.assertEqual(json.loads(value)["format"],"workload-failure-v1")

    def test_full_structured_output_preserves_types_fields_and_array_order(self):
        a = task.canonical_json(task.strict_loads(b'{"a":[1,true],"b":"\\u03bb"}'))
        b = task.canonical_json(task.strict_loads('{"b":"λ","a":[1,true]}'.encode()))
        self.assertEqual(a,b)
        self.assertNotEqual(a,task.canonical_json(task.strict_loads(b'{"a":[true,1],"b":"\\u03bb"}')))
        with self.assertRaises(ValueError):
            task.strict_loads(b'{"a":1,"a":2}')

    def test_driver_exposes_untimed_correctness_mode_and_no_performance_clock(self):
        source = (Path(__file__).resolve().parents[3]/"environment/workload/driver/src/main.rs").read_text()
        self.assertIn("--check-batch",source)
        self.assertEqual(source.count("if resident { stop(); }"),2)
        self.assertNotIn("Instant",source)
        self.assertNotIn("elapsed(",source)

    def test_complete_history_paths_follow_registry_spec(self):
        self.assertEqual(corpus.index_path("a"),"1/a")
        self.assertEqual(corpus.index_path("cc"),"2/cc")
        self.assertEqual(corpus.index_path("syn"),"3/s/syn")
        self.assertEqual(corpus.index_path("serde_json"),"se/rd/serde_json")

    def test_family_is_broader_than_one_repository(self):
        self.assertEqual(corpus.family("https://github.com/Example/One.git"),
                         corpus.family("https://github.com/example/two/tree/main"))
        self.assertNotEqual(corpus.family("https://github.com/example/one"),
                            corpus.family("https://github.com/unrelated/one"))

    def test_repository_provenance_never_records_credentials(self):
        self.assertIsNone(corpus.repository("https://user:credential@example.org/repo/code"))


if __name__ == "__main__":
    unittest.main()
