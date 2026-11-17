import importlib.util
import hashlib
from pathlib import Path
import unittest

from judge.score_result import interpret
from judge.fuzzing_generic import PROFILE
from tools import judge_control
from tools.judge_control import control_errors, declared_units, task_units

ROOT = Path(__file__).resolve().parents[2]
# Inventories previously hard-coded in tools.judge_control; tests bind the
# derived inventories to them.
GIT, SQLGLOT = "git-history-change-summary", "sqlglot-mozilla-transpile"
TASK_UNITS = {
    GIT: ("patch-myers", "shortstat-myers", "patch-histogram", "patch-ignore-all-space",
          "patch-patience", "patch-full-context", "merge-file"),
    SQLGLOT: ("batch", "optimizer", "lineage", "diff"),
}


def pristine(units=TASK_UNITS[SQLGLOT]):
    measurement = {
        "correct": True, "quality": {"noisy": False}, "speedup": 1,
        "wall_statistics": {"ci95": [.99, 1.01]},
        "cases": {name: {
            "aa": {"status": "resolved", "ci95": [.995, 1.005],
                   "sample_count": 10, "relative_half_width": .005},
            "ab": {"status": "unresolved", "ci95": [.99, 1.01],
                   "sample_count": 30, "relative_half_width": .01},
        } for name in units},
    }
    return measurement


def result_for(measurement):
    return {**interpret(measurement), "public_only": False, "gate_profile": PROFILE,
            "generic_gates": {"passed": True}, "cpu_correctness_gate": {"passed": True}}


class ControlTests(unittest.TestCase):
    def check(self, measurement, result=None):
        return control_errors(result_for(measurement) if result is None else result,
                              measurement, TASK_UNITS[SQLGLOT])

    def test_real_pristine_interpretation_with_unresolved_ab_is_valid(self):
        self.assertEqual(self.check(pristine()), [])

    def test_real_scorer_biased_and_unresolved_aa_do_not_qualify(self):
        # [1.03, 1.04] is off-centre beyond the ±2.5% equivalence band.
        for change in ({"ci95": [1.03, 1.04]}, {"status": "unresolved"}):
            with self.subTest(change=change):
                measurement = pristine()
                measurement["cases"]["lineage"]["aa"].update(change)
                result = result_for(measurement)
                self.assertTrue(result["aa_precision"]["passed"])
                self.assertEqual(result["status"], "inconclusive")
                self.assertTrue(self.check(measurement, result))

    def test_host_noise_rejects_even_when_scorer_precision_passes(self):
        measurement = pristine()
        measurement["quality"]["noisy"] = True
        self.assertTrue(self.check(measurement))

    def test_empty_or_partial_units_public_scope_and_legacy_are_rejected(self):
        measurement = pristine()
        for key, value in (("cases", {}), ("public_only", True), ("public_only", None),
                           ("gate_profile", "legacy"), ("reason_codes", ["future_unknown"]),
                           ("cpu_correctness_gate", {"passed": False})):
            result = result_for(measurement)
            result[key] = value
            self.assertTrue(self.check(measurement, result), key)
        measurement["cases"].pop("diff")
        self.assertTrue(self.check(measurement))

    def test_width_and_actual_aa_interval_are_checked_not_only_summary(self):
        measurement = pristine()
        result = result_for(measurement)
        measurement["cases"]["batch"]["aa"]["ci95"] = [.97, 1.03]
        self.assertTrue(self.check(measurement, result))

    def test_aa_full_width_limit_is_5_pp(self):
        for width, ok in ((.04, True), (.0501, False)):
            with self.subTest(width_pp=100 * width):
                measurement = pristine()
                clean = result_for(measurement)
                measurement["cases"]["batch"]["aa"].update(ci95=[1 - width / 2, 1 + width / 2],
                                                           relative_half_width=width / 2)
                if ok:  # the judge's reduced result agrees and passes
                    self.assertEqual(self.check(measurement), [])
                else:  # the raw interval is checked even when the reduced result looks clean
                    self.assertIn("batch: A/A full width exceeds 5 pp", self.check(measurement, clean))
                    self.assertTrue(self.check(measurement))

    def test_raw_reduced_disagreement_and_missing_quality_reject(self):
        measurement = pristine()
        result = result_for(measurement)
        result["cases"]["diff"]["pairs"] += 1
        self.assertTrue(self.check(measurement, result))
        del measurement["quality"]
        self.assertTrue(self.check(measurement, result))

    def test_malformed_intervals_fail_closed(self):
        for ci in ([float("nan"), 1], [1, float("inf")], [0, 1], [1.1, .9], [], None):
            measurement = pristine()
            result = result_for(measurement)
            measurement["cases"]["diff"]["aa"]["ci95"] = ci
            self.assertTrue(self.check(measurement, result), repr(ci))

    def test_aggregate_need_not_contain_one_when_judge_reports_inconclusive(self):
        # Intel c4 sqlglot rc.7 control: correct, inconclusive, every A/A unit
        # unbiased and precise, reason aggregate_within_aa_noise, aggregate
        # [0.9969, 0.9996]. The judge's own standard holds; it must qualify.
        measurement = pristine()
        widths = {"batch": .0051, "optimizer": .0109, "lineage": .0075, "diff": .0090}
        for name, width in widths.items():
            measurement["cases"][name]["aa"].update(
                ci95=[1 - width / 2, 1 + width / 2], relative_half_width=width / 2, sample_count=15)
            measurement["cases"][name]["ab"].update(status="resolved", ci95=[.993, 1.003])
        measurement["wall_statistics"]["ci95"] = [.9969, .9996]
        result = result_for(measurement)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason_codes"], ["aggregate_within_aa_noise"])
        self.assertEqual(result["candidate_ci95"], [.9969, .9996])
        self.assertEqual(self.check(measurement, result), [])

    def test_speedup_or_slowdown_status_still_fails(self):
        # A bias larger than the A/A envelope is reported as a speedup or
        # slowdown by the judge, never inconclusive, so the control fails.
        for status, ci in (("speedup", [1.01, 1.02]), ("slowdown", [.98, .99])):
            with self.subTest(status=status):
                measurement = pristine()
                for arms in measurement["cases"].values():
                    arms["ab"]["status"] = "resolved"
                measurement["wall_statistics"]["ci95"] = ci
                measurement["speedup"] = sum(ci) / 2
                result = result_for(measurement)
                self.assertEqual(result["status"], status)
                self.assertIn("Pristine control must be correct and inconclusive",
                              self.check(measurement, result))

    def test_malformed_aggregate_interval_still_fails(self):
        for ci in ([float("nan"), 1], [1, float("inf")], [0, 1], [1.1, .9], [1], None, "x"):
            with self.subTest(ci=ci):
                measurement = pristine()
                result = result_for(measurement)
                result["candidate_ci95"] = ci
                self.assertIn("Pristine aggregate interval must be finite and ordered",
                              self.check(measurement, result))

    def test_cli_reports_aggregate_containment_as_information_only(self):
        import contextlib
        import io
        import json
        import sys
        import tempfile
        from unittest.mock import patch
        from tools import judge_control
        task = GIT
        measurement = pristine(TASK_UNITS[GIT])
        measurement["wall_statistics"]["ci95"] = [.9969, .9996]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps({**result_for(measurement), "task": task}))
            (Path(tmp) / "measurements.json").write_text(json.dumps(measurement))
            out = io.StringIO()
            with patch.object(sys, "argv", ["judge_control", "--result", str(path), "--task", task]), \
                    contextlib.redirect_stdout(out):
                code = judge_control.main()
        report = json.loads(out.getvalue())
        self.assertEqual((code, report["passed"], report["errors"]), (0, True, []))
        self.assertEqual(report["aggregate_ci95"], [.9969, .9996])
        self.assertIs(report["aggregate_contains_one"], False)

    def test_task_units_match_protected_public_and_heldout_definitions(self):
        from judge.verifier import case_roles, performance_units
        import json
        import tempfile
        from unittest.mock import patch
        for name, expected in TASK_UNITS.items():
            task = ROOT / "tasks" / name
            spec = importlib.util.spec_from_file_location("control_inventory", task / "environment/workload/task.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            settings = json.loads((task / "tests/judge.json").read_text())
            for public in (True, False):
                inputs = task / ("environment/workload/inputs" if public else "tests/heldout")
                if public:
                    rows = [doc for split in json.loads((inputs / "manifest.json").read_text())[
                        "splits"].values() for batch in split for doc in batch["docs"]]
                    if name == GIT:
                        # This checks protected case IDs and unit mapping, not corpus authentication.
                        with tempfile.TemporaryDirectory() as temporary:
                            synthetic = Path(temporary)
                            (synthetic / "files").mkdir()
                            body = b'{"repositories":[]}'
                            digest = hashlib.sha256(body).hexdigest()
                            (synthetic / "files" / digest).write_bytes(body)
                            documents = [{**row, "sha256": digest} for row in rows]
                            with patch.object(module, "documents", return_value=documents):
                                cases = {c.id: c for c in module.cases(synthetic)}
                    else:
                        with patch.object(module, "documents", return_value=rows):
                            cases = {c.id: c for c in module.cases(inputs)}
                    roles = case_roles(settings, cases, public)
                    units = performance_units(settings, roles, public)
                    names = set(units) if units is not None else {
                        n for n, role in roles.items() if role == "performance"}
                elif "performance_units" in settings:
                    names = set(settings["performance_units"]["heldout"])
                    self.assertEqual(task_units(name), sorted(expected))
                else:
                    # Exercise the same implicit family derivation without private manifests or bodies.
                    manifest = json.loads((task / "environment/workload/inputs/manifest.json").read_text())
                    documents = [d for split in manifest["splits"].values() for batch in split for d in batch["docs"]]
                    names = {("batch" if d.get("family", "transpile") == "transpile"
                              else d["family"])
                             for split in manifest["splits"].values()
                             for batch in split for d in batch["docs"]}
                    self.assertEqual(self.derive_with_manifest_documents(name, documents), sorted(expected))
                self.assertEqual(set(expected), names)

    @staticmethod
    def derive_with_manifest_documents(name, documents=None):
        """Run the real implicit derivation (protected task.py) on manifest documents.

        Protected public metadata can supply the unit-only fixture when the
        private manifest itself is absent. This is not payload authentication.
        """
        import json
        import sys
        import types
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / "tools/runner"))
        try:
            import runner
        finally:
            sys.path.remove(str(ROOT / "tools/runner"))
        stub = types.ModuleType("runner")
        stub.__dict__.update(runner.__dict__)
        stub.documents = lambda inputs: documents if documents is not None else [
            d for split in json.loads((Path(inputs) / "manifest.json").read_text())[
                "splits"].values() for batch in split for d in batch["docs"]]
        with patch.dict(sys.modules, {"runner": stub}):
            return task_units(name)

    def test_every_declared_task_derives_its_inventory(self):
        import json
        tasks = [t for t in sorted((ROOT / "tasks").iterdir()) if (t / "tests/judge.json").is_file()]
        declared = [t for t in tasks if "performance_units" in json.loads((t / "tests/judge.json").read_text())]
        self.assertTrue({GIT, "sqlite-distinct-plant-facets", "numpy-quantile"} <=
                        {task.name for task in declared})
        for task in declared:
            with self.subTest(task=task.name):
                expected = sorted(json.loads((task / "tests/judge.json").read_text())["performance_units"]["heldout"])
                self.assertTrue(expected)
                self.assertEqual(task_units(task.name), expected)
        for task in tasks:
            if task not in declared:
                with self.subTest(task=task.name):
                    self.assertIsNone(declared_units(task.name))

    def test_implicit_units_derive_from_task_py_on_a_fixture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "tasks/fixture-task"
            (task / "tests/heldout").mkdir(parents=True)
            (task / "environment/workload").mkdir(parents=True)
            (root / "tools/runner").mkdir(parents=True)
            (task / "tests/judge.json").write_text('{"case_roles": {"heldout": {"check": "correctness"}}}')
            (task / "environment/workload/task.py").write_text(
                "from types import SimpleNamespace as Case\n"
                "def cases(inputs):\n"
                "    assert inputs.name == 'heldout'\n"
                "    return [Case(id=n) for n in ('two', 'one', 'check')]\n")
            self.assertEqual(task_units("fixture-task", root), ["one", "two"])
            (task / "environment/workload/task.py").write_text(
                "def cases(inputs):\n    raise RuntimeError('held-out bodies not authenticated')\n")
            with self.assertRaisesRegex(ValueError, "scoring VM"):
                task_units("fixture-task", root)

    def test_missing_or_malformed_definitions_fail_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            judge = root / "tasks/fixture-task/tests/judge.json"
            with self.assertRaisesRegex(ValueError, "unavailable"):
                task_units("fixture-task", root)
            judge.parent.mkdir(parents=True)
            for body in ("not json", "[]", '{"performance_units": {}}', '{"performance_units": {"heldout": {}}}',
                         '{"performance_units": {"heldout": {"unit": []}}}',
                         '{"performance_units": {"heldout": ["unit"]}}', '{"performance_units": []}'):
                with self.subTest(body=body):
                    judge.write_text(body)
                    with self.assertRaises(ValueError):
                        task_units("fixture-task", root)
            for task in ("", "../git", "Bad"):
                with self.assertRaises(ValueError):
                    task_units(task, root)

    def write_control(self, directory, task, units, policy="p" * 64):
        import json
        measurement = pristine(units)
        result = {**result_for(measurement), "task": task,
                  "registration_attempt": {"links": {"task_policy_sha256": policy}}}
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(result))
        (directory / "measurements.json").write_text(json.dumps(measurement))
        return directory / "result.json"

    def test_on_vm_record_binds_units_bytes_task_and_policy(self):
        import hashlib
        import tempfile
        from judge.host_registry import task_policy_sha256
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_control(Path(tmp), GIT, TASK_UNITS[GIT])
            record = judge_control.validation_record(path, GIT)
            self.assertEqual(record["errors"], [])
            self.assertEqual(record["units"], sorted(TASK_UNITS[GIT]))
            self.assertEqual(record["units_source"], "task definition")
            self.assertEqual(record["task_policy_sha256"], task_policy_sha256(GIT))
            self.assertEqual(record["result_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_off_vm_consumer_uses_bound_vm_record_and_rejects_mismatches(self):
        import hashlib
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            off_vm = Path(tmp) / "no-checkout"
            path = self.write_control(Path(tmp) / "control", SQLGLOT, TASK_UNITS[SQLGLOT])
            self.assertIn("No VM-recorded validation", judge_control.validate_files(path, SQLGLOT, off_vm)[0])
            good = {"passed": True, "errors": [], "task": SQLGLOT, "units": sorted(TASK_UNITS[SQLGLOT]),
                    "units_source": "task definition", "task_policy_sha256": "p" * 64,
                    "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            record = path.parent / judge_control.RECORD
            record.write_text(json.dumps(good))
            self.assertEqual(judge_control.validate_files(path, SQLGLOT, off_vm), [])
            self.assertEqual(judge_control.validation_record(path, SQLGLOT, off_vm)["units_source"], "vm record")
            for key, value in (("result_sha256", "0" * 64), ("task", GIT), ("task_policy_sha256", "q" * 64),
                               ("passed", False), ("units", []), ("units", ["batch"])):
                with self.subTest(key=key, value=value):
                    record.write_text(json.dumps({**good, key: value}))
                    self.assertTrue(judge_control.validate_files(path, SQLGLOT, off_vm))
            # The record cannot vouch for different result bytes, even with valid statistics.
            record.write_text(json.dumps(good))
            path.write_text(path.read_text() + " ")
            self.assertTrue(judge_control.validate_files(path, SQLGLOT, off_vm))

if __name__ == "__main__":
    unittest.main()
