"""Registration shape and admission checks; real Docker receipts remain separate."""
import importlib.util
from pathlib import Path
import tempfile
import types
import sys
import unittest

ROOT = Path(__file__).resolve().parent
PACKAGE = "_sqlglot_rc_registration_test"


def hooks():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
    spec = importlib.util.spec_from_file_location(PACKAGE + ".rc_hooks", ROOT / "rc_hooks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RegistrationTests(unittest.TestCase):
    def test_only_supported_task_callbacks_are_registered(self):
        module = hooks()
        self.assertEqual(set(module.GATES), {"stream_faults", "repeat_in_process", "aliasing"})
        self.assertTrue(all(callable(value) for value in module.GATES.values()))
        self.assertTrue(callable(module.PRECONDITION))
        self.assertTrue(callable(module.COMPARE))

    def test_cli_requests_are_real_small_and_large_stdin_inputs(self):
        module = hooks()
        with tempfile.TemporaryDirectory() as directory:
            request = module.CLI_CASES({"out": Path(directory)})
            self.assertTrue(request["applicable"])
            self.assertEqual([value["payload_class"] for value in request["cases"]], ["small", "large"])
            for spec in request["cases"]:
                case, surface = spec["case"], spec["surface"]
                self.assertTrue(module.CLI_PRECONDITION(case, surface)["valid"])
                sql = (Path(spec["inputs"]) / "input.sql").read_text()
                self.assertEqual(sql, case["input"]["sql"])
                self.assertEqual(spec["step"]["stdin_file"], "input.sql")
                self.assertEqual(spec["step"]["argv"][2:4], ["sqlglot", "-"])
                self.assertEqual(set(spec["faults"]),
                                 {"healthy", "dev_full", "closed_reader", "closed_stdout"})
            self.assertGreater(len(request["cases"][1]["case"]["input"]["sql"]), 65536)
            self.assertIn("authority", request["readonly_applicability_evidence"])

    def test_cli_admission_rejects_private_surface_wrong_task_and_oversize(self):
        module = hooks()
        with tempfile.TemporaryDirectory() as directory:
            spec = module.CLI_CASES({"out": Path(directory)})["cases"][0]
        for key, value in (("visibility", "private"), ("contract_id", "unknown")):
            surface = {**spec["surface"], key: value}
            self.assertFalse(module.CLI_PRECONDITION(spec["case"], surface)["valid"])
        for key, value in (("task", "another-task"), ("input", {"sql": "x" * 131073})):
            case = {**spec["case"], key: value}
            self.assertFalse(module.CLI_PRECONDITION(case, spec["surface"])["valid"])

    def test_header_request_uses_authenticated_pinned_feature_evidence(self):
        request = hooks().HEADER_CONSUMERS({})
        self.assertFalse(request["applicable"])
        evidence = request["applicability_evidence"]
        self.assertEqual(evidence["upstream_commit"], "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76")
        self.assertEqual(evidence["scan"]["header_paths"], [])
        self.assertIn("pure-Python", request["reason"])

    def test_public_callback_schedule_is_explicit_and_bounded(self):
        module = hooks()
        adapter = sys.modules[PACKAGE + ".adapter"]
        import json
        surfaces = {row["id"]: row for row in
                    json.loads((ROOT / "surface.json").read_text())["surfaces"]}
        for name in ("sqlglot.expressions.Expression.transform", "sqlglot.expressions.Expression.walk"):
            case = adapter.make_case(name, "SELECT a + 1 AS b FROM t")
            case["callback_fault"] = {"errno": 28, "after": 3, "copy": True}
            self.assertTrue(module.PRECONDITION(case, surfaces[name])["valid"])
            for field, invalid in (("errno", 999), ("after", True), ("after", 0), ("copy", 1)):
                changed = {**case, "callback_fault": {**case["callback_fault"], field: invalid}}
                self.assertFalse(module.PRECONDITION(changed, surfaces[name])["valid"])
        case = adapter.make_case("sqlglot.transpile", "SELECT 1")
        case["callback_fault"] = {"errno": 28, "after": 3, "copy": True}
        self.assertFalse(module.PRECONDITION(case, surfaces[case["surface_id"]])["valid"])


if __name__ == "__main__":
    unittest.main()
