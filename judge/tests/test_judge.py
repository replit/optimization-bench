import json
from pathlib import Path
import random
import tempfile
import unittest

from judge import calibration, heldout, policy, scheduler, statistics, upstream, telemetry, source_policy


class StatisticsTests(unittest.TestCase):
    def test_bootstrap_retains_outlier(self):
        pairs = [(100, 50)] * 7 + [(1000, 1)]
        result = statistics.bootstrap(pairs)
        self.assertEqual(result["pairs"][-1], [1000, 1])
        self.assertLessEqual(result["ci95"][0], result["speedup"])
        self.assertGreaterEqual(result["ci95"][1], result["speedup"])

    def test_adaptive(self):
        self.assertFalse(statistics.stopping([(10, 10)] * 4)["stop"])
        self.assertEqual(statistics.stopping([(10, 10)] * 5)["status"], "resolved")
        result = statistics.stopping([(100, 1), (1, 100)] * 3, maximum=6)
        self.assertTrue(result["stop"])
        self.assertEqual(result["status"], "not resolved")

    def test_classes_not_pooled_and_all_correct(self):
        a = {"cpu_class": "A", "correct": True, "speedup": 4}
        b = {"cpu_class": "B", "correct": True, "speedup": 1}
        result = statistics.aggregate_classes([a, b])
        self.assertEqual(result["equal_weight_speedup"], 2)
        b["correct"] = False
        self.assertFalse(statistics.aggregate_classes([a, b])["correct"])
        with self.assertRaises(ValueError):
            statistics.aggregate_classes([a, a])

    def test_randomized_balanced_orders(self):
        a = scheduler.orders(30, random.Random(5))
        b = scheduler.orders(30, random.Random(6))
        self.assertNotEqual(a, b)
        self.assertEqual(a.count(("baseline", "candidate")), 15)
        self.assertEqual(a.count(("candidate", "baseline")), 15)

    def test_aa_and_warmup_and_correctness_every_run(self):
        seen = []
        def measure(case, build):
            seen.append(build)
            return {"correct": True, "ns": 10}
        result = scheduler.session(["case"], measure, maximum=5)
        self.assertTrue(result["aa_passed"])
        self.assertEqual(len(result["records"]), 24)
        self.assertEqual(sum(r["arm"] == "aa" and r["build"] == "baseline"
                             for r in result["records"]), 12)
        with self.assertRaises(scheduler.IncorrectCandidate):
            scheduler.session(["case"], lambda *_: {"correct": False, "ns": 1})

    def test_calibration_expiry_and_identity(self):
        host = {"sha256": "same", "cpu_class": "A"}
        result = {"correct": True, "aa_passed": True, "status": "resolved",
                  "quality": {"noisy": False}}
        receipt = calibration.receipt(result, host, "image", now=10)
        calibration.require(receipt, host, "image", now=11)
        for changed_host, image, now in [(host, "other", 11),
                                        (host, "image", 86411),
                                        ({"sha256": "other", "cpu_class": "A"}, "image", 11)]:
            with self.assertRaises(ValueError):
                calibration.require(receipt, changed_host, image, now)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pristine = self.root / "pristine"
        (self.pristine / "lib").mkdir(parents=True)
        (self.pristine / "lib/main.py").write_text("x = 1\n")
        (self.pristine / "lib/protected.py").write_text("fixed\n")
        self.policy = {"allowed_extensions": [".py"], "source_roots": ["lib"],
                       "protected_paths": ["protected.py"], "max_source_files": 5,
                       "max_source_bytes": 1000}

    def tearDown(self):
        self.temp.cleanup()

    def test_artifacts_to_envelope(self):
        source = self.root / "artifact"
        import shutil
        shutil.copytree(self.pristine / "lib", source)
        (source / "main.py").write_text("x = 2\n")
        result = policy.artifacts_to_envelope({"lib": source}, self.pristine,
                                              self.root / "replay", self.policy)
        self.assertEqual(result["files"], {"lib/main.py": "x = 2\n"})

    def test_protected_file_change_and_out_of_scope(self):
        import shutil
        candidate = self.root / "candidate"
        shutil.copytree(self.pristine, candidate)
        (candidate / "lib/protected.py").write_text("changed")
        with self.assertRaises(ValueError):
            policy.envelope(candidate, self.pristine, self.policy)
        with self.assertRaises(ValueError):
            policy.source_policy.validate({"../main.py": "x"}, self.policy)
        with self.assertRaises(ValueError):
            policy.source_policy.validate({"other/main.py": "x"}, self.policy)

    def test_modify_only_without_explicit_allowlist_fails_closed(self):
        for value in ("missing", None, "lib/main.py"):
            rule = {**self.policy, "operations": ["modify"]}
            if value != "missing":
                rule["allowed_files"] = value
            with self.subTest(allowed_files=value):
                with self.assertRaisesRegex(ValueError, "modify-only directory scope requires allowed_files"):
                    source_policy.validate({"lib/main.py": "x = 1\n"}, rule)

    def test_modify_only_and_file_limit(self):
        exact = {**self.policy, "allowed_files": ["lib/main.py"], "operations": ["modify"]}
        with self.assertRaises(ValueError):
            policy.source_policy.validate({}, exact)
        with self.assertRaises(ValueError):
            policy.source_policy.validate({"lib/main.py": "x", "lib/extra.py": "x"}, exact)
        with self.assertRaises(ValueError):
            policy.source_policy.validate({"lib/main.py": "x" * 1001}, self.policy)

    def test_ruff_inline_tests_and_docs(self):
        import shutil
        original = "/// docs\nfn run() {}\n#[cfg(test)]\nmod tests {}\n"
        (self.pristine / "lib/main.py").unlink()
        (self.pristine / "lib/protected.py").unlink()
        (self.pristine / "lib/main.rs").write_text(original)
        rules = {**self.policy, "allowed_extensions": [".rs"],
                 "allowed_files": ["lib/main.rs"],
                 "inline_protection": {"lib/main.rs": policy.ruff_source_policy.commitment(original)}}
        candidate = self.root / "candidate"
        shutil.copytree(self.pristine, candidate)
        policy.envelope(candidate, self.pristine, rules)
        for mutation in (original.replace("mod tests {}", "mod tests { changed }"),
                         original.replace("/// docs", "/// changed")):
            (candidate / "lib/main.rs").write_text(mutation)
            with self.assertRaises(ValueError):
                policy.envelope(candidate, self.pristine, rules)

    def test_symlink_and_protected_addition(self):
        source = self.root / "artifact"
        import shutil
        shutil.copytree(self.pristine / "lib", source)
        (source / "data.txt").write_text("unapproved")
        with self.assertRaises(ValueError):
            policy.artifacts_to_envelope({"lib": source}, self.pristine,
                                          self.root / "replay", self.policy)
        (source / "data.txt").unlink()
        (source / "linked.py").symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):
            policy.artifacts_to_envelope({"lib": source}, self.pristine,
                                          self.root / "replay2", self.policy)

    def test_pristine_dependency_link_not_an_artifact(self):
        (self.pristine / "node_modules").symlink_to("/app/deps/node_modules")
        result = policy.artifacts_to_envelope({"lib": self.pristine / "lib"},
                                              self.pristine, self.root / "replay", self.policy)
        self.assertEqual(result["files"], {"lib/main.py": "x = 1\n"})

    def test_protected_upstream_symlink_is_immutable_not_dereferenced(self):
        import shutil
        (self.pristine / "AGENTS.md").write_text("upstream instructions")
        (self.pristine / "CLAUDE.md").symlink_to("AGENTS.md")
        replay = self.root / "replay"
        policy.artifacts_to_envelope({"lib": self.pristine / "lib"},
                                     self.pristine, replay, self.policy)
        self.assertTrue((replay / "CLAUDE.md").is_symlink())
        (replay / "CLAUDE.md").unlink()
        (replay / "CLAUDE.md").symlink_to("lib/main.py")
        with self.assertRaises(ValueError):
            policy.envelope(replay, self.pristine, self.policy)


class GatesTests(unittest.TestCase):
    def test_missing_heldout_refuses_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = Path(directory)
            (tests / "heldout").mkdir()
            manifest = tests / "heldout/manifest.json"
            manifest.write_text(json.dumps({"splits": {}}))
            (tests / "heldout.lock.json").write_text(json.dumps({
                "schema_version": 1, "status": "unavailable",
                "files": {"heldout/manifest.json": heldout.sha(manifest)},
                "archives": [],
            }))
            heldout.check(tests)
            with self.assertRaisesRegex(ValueError, "PAYLOAD PENDING"):
                heldout.check(tests, require_available=True)

    def test_outcome_set_and_extra_gates(self):
        baseline = {"upstream": {"passed": True, "outcomes": {"test": "passed"}}}
        self.assertTrue(upstream.compare(baseline, baseline, "terser-minify"))
        with self.assertRaises(ValueError):
            upstream.compare(baseline, baseline, "black-project-format")
        with self.assertRaises(ValueError):
            upstream.compare(baseline, {"upstream": {"passed": True, "outcomes": {"other": "passed"}}},
                             "terser-minify")

    def test_heldout_hash_and_uncommitted_file(self):
        import shutil
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "tests"
            shutil.copytree(root / "tasks/sqlglot-mozilla-transpile/tests", target)
            (target / "heldout/uncommitted").write_text("uncommitted")
            with self.assertRaises(ValueError):
                heldout.check(target)
            (target / "heldout/uncommitted").unlink()
            (target / "heldout/manifest.json").write_text("{}")
            with self.assertRaises(ValueError):
                heldout.check(target)

    def test_noise_thresholds_and_pinning(self):
        before = {"cpu": {0: [0] * 8}, "cpu_stat": {"throttled_usec": 0},
                  "monotonic": 0, "frequency_khz": {}}
        after = {"cpu": {0: [100, 0, 0, 0, 0, 0, 0, 5]},
                 "cpu_stat": {"throttled_usec": 100000},
                 "monotonic": 1, "frequency_khz": {}}
        self.assertTrue(telemetry.quality(before, after, [0])["noisy"])
        with self.assertRaises(ValueError):
            telemetry.pinned("0")


if __name__ == "__main__":
    unittest.main()
