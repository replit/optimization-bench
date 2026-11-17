"""Contract-derived output/OS-error controls; no project-specific allowances."""
import unittest

from judge import fuzzing_outputs as output
from judge import fuzzing_io


class OutputComparatorTests(unittest.TestCase):
    def test_json_order_escapes_and_types_follow_strict_frozen_contract(self):
        json_rule, exact = {"kind": "json-structural"}, {"kind": "exact-bytes"}
        left, right = b'{"a":1,"b":[true,2],"c":"x"}', b'{"c":"\\u0078","b":[true,2],"a":1}'
        self.assertTrue(output.equal_output(output.output_value(left, json_rule),
                                            output.output_value(right, json_rule), json_rule))
        self.assertFalse(output.equal_output(output.output_value(left, exact),
                                             output.output_value(right, exact), exact))
        for changed in (b'{"a":true,"b":[true,2],"c":"x"}',
                        b'{"a":1.0,"b":[true,2],"c":"x"}',
                        b'{"a":2,"b":[true,2],"c":"x"}'):
            self.assertFalse(output.equal_output(output.output_value(left, json_rule),
                                                 output.output_value(changed, json_rule), json_rule))
        for invalid in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(output.OutputContractError):
                output.output_value(invalid, json_rule)

    def test_numeric_tolerance_is_explicit_not_integer_or_type_coercion(self):
        rule = {"kind": "numeric", "rtol": .01, "atol": 0}
        normalized = lambda raw: output.output_value(raw, rule)
        self.assertTrue(output.equal_output(normalized(b'[1.0]'), normalized(b'[1.001]'), rule))
        self.assertFalse(output.equal_output(normalized(b'[1]'), normalized(b'[2]'), rule))
        self.assertFalse(output.equal_output(normalized(b'[1]'), normalized(b'[true]'), rule))


class OSErrorIdentityTests(unittest.TestCase):
    def traceback(self, path, errno=30):
        return (f'Traceback (most recent call last):\n'
                f'  File "/usr/local/lib/python3.14/tempfile.py", line 256, in _mkstemp_inner\n'
                f'    fd = _os.open(file, flags, 0o600)\n'
                f"OSError: [Errno {errno}] Read-only file system: '{path}'\n").encode()

    def test_temp_name_is_not_identity_but_errno_and_visible_path_are(self):
        spec = {"required_outputs": ["out/result.sql"], "watch_inputs": ["input.sql"]}
        identify = lambda path, errno=30: output.error_identity(self.traceback(path, errno), spec)
        left = identify("/scratch/work/out/result.sqlabc123.sql")
        self.assertEqual(left, identify("/scratch/work/out/result.sqlzxy789.sql"))
        self.assertEqual(left["path"]["role"], "internal-temp")
        self.assertNotEqual(left, identify("/scratch/work/out/result.sqlabc123.sql", 13))
        self.assertNotEqual(left, identify("/scratch/work/out/wrong.sql"))
        self.assertNotEqual(left, output.error_identity(b"", spec))

    def test_non_os_exception_does_not_lose_its_message(self):
        self.assertNotEqual(output.error_identity(b"ValueError: [Errno 30] first\n", {}),
                            output.error_identity(b"ValueError: [Errno 30] second\n", {}))


class DriverScopeTests(unittest.TestCase):
    def test_registered_callback_presence_does_not_substitute_for_passed_evidence(self):
        from types import SimpleNamespace
        value = {"format": "judge-generic-cli-cases-v1", "applicable": False,
                 "applicability_evidence": {"reviewer": "independent scope review",
                    "covering_callbacks": ["stream_faults", "repeat_in_process"]}}
        hooks = SimpleNamespace(CLI_CASES=lambda _: value,
                                GATES={"stream_faults": lambda _: None, "repeat_in_process": lambda _: None})
        context = {"hooks": hooks, "gate_results": {
            "stream_faults": {"status": "passed"}, "repeat_in_process": {"status": "pending"}}}
        self.assertEqual(fuzzing_io.cli_gate(context)["status"], "pending")
        context["gate_results"]["repeat_in_process"]["status"] = "passed"
        self.assertEqual(fuzzing_io.cli_gate(context)["status"], "not_applicable")
        del hooks.GATES["stream_faults"]
        self.assertEqual(fuzzing_io.cli_gate(context)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
