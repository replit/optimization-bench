import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge.gates import cargo_outcomes
from judge.controls.babel_contract import archived_passed
from judge.controls.unittest_gate import Result, without_excluded
from judge.gate_contracts import summaries
from judge.source_policy import allowed
from judge.upstream import unittest_contract
from judge import build as builder


class UpstreamContractsTests(unittest.TestCase):
    def test_compiler_seed_requires_matching_image_and_commitments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "ruff-project-lint"
            task.mkdir()
            rules = {"source_roots": ["lib"], "allowed_extensions": [".rs"],
                     "allowed_files": ["lib/main.rs"], "protected_paths": [],
                     "operations": ["modify"], "max_source_files": 2, "max_source_bytes": 1000}
            (task / "release.json").write_text(json.dumps({"source_policy": rules}))
            source = root / "pristine"
            (source / "lib").mkdir(parents=True)
            (source / "lib/main.rs").write_text("fn main() {}\n")
            seed = root / "seed"
            (seed / "source/lib").mkdir(parents=True)
            (seed / "source/lib/main.rs").write_text("fn main() {}\n")
            (seed / "build").mkdir()
            (seed / "build/ruff").write_text("compiled artifact")
            digest = builder.source_policy.digest({"lib/main.rs": "fn main() {}\n"})
            for image, source_digest, build_digest in [
                ("other", digest, "wrong"), ("image", "wrong", "wrong"), ("image", digest, "wrong")
            ]:
                (seed / "build-receipt.json").write_text(json.dumps({
                    "task": task.name, "image": image, "source_sha256": source_digest,
                    "build_sha256": build_digest}))
                with patch.object(builder, "image_id", return_value="image"), patch.object(builder, "pinned"):
                    with self.assertRaises(ValueError):
                        builder.build(task, source, "image", "0-7", root / "output",
                                      source=source, seed_build=seed)
                self.assertFalse((root / "output").exists())
    def test_unittest_exclusions_are_exact_and_fail_closed(self):
        class Fixture(unittest.TestCase):
            def test_kept(self): pass
            def test_tpch(self): pass
            def test_tpcds(self): pass
        def suite():
            loaded = unittest.defaultTestLoader.loadTestsFromTestCase(Fixture)
            return unittest.TestSuite([unittest.TestSuite([loaded])])
        prefix = Fixture("test_kept").id().rsplit(".", 1)[0] + "."
        declared = [prefix + "test_tpch", prefix + "test_tpcds"]
        tests = suite()
        self.assertEqual(without_excluded(tests, declared), sorted(declared))
        self.assertEqual(tests.countTestCases(), 1)
        self.assertEqual(without_excluded(suite(), []), [])
        for wrong in ([prefix + "test_missing"], declared + [declared[0]], declared[:1] + [prefix + "nope"]):
            with self.assertRaises(RuntimeError):
                without_excluded(suite(), wrong)

    def test_subtest_projection_strips_only_default_object_repr_pointers(self):
        a = {"subtests": [{"identity": "test [<fixture.<locals>.Point object at 0x1234>]", "status": "passed"},
                          {"identity": "test (sql='0x1234')", "status": "passed"}]}
        b = {"subtests": [{"identity": "test [<fixture.<locals>.Point object at 0x9876>]", "status": "passed"},
                          {"identity": "test (sql='0x1234')", "status": "passed"}]}
        self.assertEqual(unittest_contract(a), unittest_contract(b))
        self.assertIn(a["subtests"][1], unittest_contract(a)["subtests"])
        b["subtests"].reverse()
        self.assertEqual(unittest_contract(a), unittest_contract(b))
        b["subtests"].reverse()
        b["subtests"][1]["identity"] = "test (sql='0x5678')"
        self.assertNotEqual(unittest_contract(a), unittest_contract(b))
    def test_exact_rust_scope_retains_real_sys_modules(self):
        name = "crates/ruff_python_stdlib/src/sys/mod.rs"
        policy = {"source_roots": ["crates/ruff_python_stdlib/src"], "allowed_extensions": [".rs"],
                  "allowed_files": [name], "protected_paths": []}
        self.assertTrue(allowed(name, policy))
        self.assertFalse(allowed(name.replace("mod.rs", "unlisted.rs"), policy))

    def test_babel_expected_failure_is_a_real_status_contract(self):
        result = {"exit_code": 1, "passed": 109, "failed": 8, "total": 117,
                  "identities_match": True, "fixtures_unchanged": True}
        self.assertTrue(archived_passed("target117", 1, result))
        self.assertFalse(archived_passed("target117", 0, result))
        self.assertFalse(archived_passed("historical120", 1, result))
        self.assertFalse(archived_passed("target117", 1, {**result, "passed": 108, "failed": 9}))

    def test_cargo_normalizes_binary_fingerprint_not_test_identity(self):
        log = "Running unittests src/lib.rs (/scratch/target/debug/deps/ruff-abcdef)\n"
        log += "test actual::identity ... ok\n"
        log += "test result: ok. 1 passed; 0 failed; 0 ignored;\n"
        a = cargo_outcomes(log, None)
        b = cargo_outcomes(log.replace("abcdef", "654321"), None)
        self.assertEqual(a, b)
        self.assertEqual(a["tests"][0]["name"], "actual::identity")
        with self.assertRaises(ValueError):
            cargo_outcomes(log.replace("1 passed", "2 passed"), None)

    def test_cargo_keeps_repeated_names_in_their_actual_suites(self):
        log = ""
        for target in ("first", "second"):
            log += f"Running unittests src/lib.rs (/scratch/target/debug/deps/{target}-abcdef)\n"
            log += "test repeated::identity ... ok\n"
            log += "test result: ok. 1 passed; 0 failed; 0 ignored;\n"
        value = cargo_outcomes(log, None)
        self.assertEqual(len(value["tests"]), 2)
        self.assertNotEqual(value["tests"][0]["target"], value["tests"][1]["target"])

    def test_cargo_negative_control_keeps_failed_identity(self):
        log = "Running unittests src/lib.rs (/scratch/target/debug/deps/ruff-abcdef)\n"
        log += "test broken::identity ... FAILED\n"
        log += "test result: FAILED. 0 passed; 1 failed; 0 ignored;\n"
        with self.assertRaises(ValueError):
            cargo_outcomes(log, None)
        value = cargo_outcomes(log, None, allow_failures=True)
        self.assertEqual(value["tests"][0]["status"], "FAILED")

    def test_unittest_retains_every_subtest(self):
        class Example(unittest.TestCase):
            def runTest(self):
                for number in range(3):
                    with self.subTest(number=number):
                        self.assertGreaterEqual(number, 0)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(Example())
        self.assertEqual(len(result.subtests), 3)
        self.assertEqual(len(result.outcomes), 1)
        self.assertTrue(all(row["status"] == "passed" for row in result.subtests))

    def test_pytest_summary_includes_setup_skip_and_teardown_failure(self):
        suites = {"upstream": {"outcomes": {"collected": 3, "outcomes": {
            "pass": {"setup": "passed", "call": "passed", "teardown": "passed"},
            "skip": {"setup": "skipped", "teardown": "passed"},
            "fail": {"setup": "passed", "call": "passed", "teardown": "failed"}}}}}
        self.assertEqual(summaries("sqlfluff-project-lint", suites)["upstream"],
                         {"passed": 1, "skipped": 1, "failed": 1, "collected": 3})
