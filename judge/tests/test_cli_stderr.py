import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import differential
from judge.cli_stderr import normalize_cli_observation, normalize_stderr, stderr_contract
from tools.runner.runner import Case, StepResult, run_case


HEAD = "panic: runtime error: slice bounds out of range [140:139]"
FIRST = (HEAD + "\n\ngoroutine 1 [running]:\nmain.oldHelper()\n\t/a.go:123 +0x23456\n").encode()
SECOND = (HEAD + " [recovered, repanicked]\n\t" + HEAD +
          "\n\ngoroutine 39 [running]:\nmain.formatStatements()\n\t/b.go:456 +0x789ab\n").encode()


def cli(stderr=FIRST):
    return {"exit_code": 2, "stdout_base64": base64.b64encode(b"output").decode(),
            "stderr_base64": base64.b64encode(stderr).decode(),
            "files_base64": {"input.sql": base64.b64encode(b"original").decode()}}


class StderrContractTests(unittest.TestCase):
    def test_same_go_headline_different_frames_and_repanic_annotation_pass(self):
        self.assertEqual(normalize_stderr(FIRST), normalize_stderr(SECOND))
        self.assertEqual(normalize_stderr(FIRST), (HEAD + "\n").encode())
        self.assertEqual(stderr_contract(FIRST)["class"], "panic")
        self.assertEqual(stderr_contract(FIRST), stderr_contract(normalize_stderr(FIRST)))
        changed = SECOND.replace(b"[140:139]", b"[141:139]")
        self.assertNotEqual(stderr_contract(FIRST), stderr_contract(changed))

    def test_stdout_status_and_file_side_effects_are_never_suppressed(self):
        expected = normalize_cli_observation(cli())
        for key, value in (("exit_code", 0), ("stdout_base64", "Y2hhbmdlZA=="),
                           ("files_base64", {"input.sql": "cmV3cml0dGVu"})):
            with self.subTest(key=key):
                changed = cli(SECOND)
                changed[key] = value
                self.assertNotEqual(expected, normalize_cli_observation(changed))

    def test_nested_cli_schemas_and_hex_encoding_keep_raw_observations(self):
        original = {"exit_code": 0, "result": [cli()], "stderr": "", "leftovers": 0}
        saved = copy.deepcopy(original)
        candidate = copy.deepcopy(original)
        candidate["result"][0] = cli(SECOND)
        self.assertEqual(normalize_cli_observation(original), normalize_cli_observation(candidate))
        self.assertEqual(original, saved)
        self.assertEqual(normalize_cli_observation(
            {"code": 2, "stdout_hex": "", "stderr_hex": FIRST.hex()}),
            normalize_cli_observation({"code": 2, "stdout_hex": "", "stderr_hex": SECOND.hex()}))

    def test_arbitrary_application_json_and_file_payloads_are_untouched(self):
        document = {"stderr": SECOND.decode(), "user": {"stderr_base64": "not a receipt"}}
        self.assertEqual(normalize_cli_observation(document), document)
        record = cli()
        record["files_base64"]["stderr_base64"] = base64.b64encode(SECOND).decode()
        self.assertEqual(normalize_cli_observation(record)["files_base64"], record["files_base64"])

    def test_python_and_javascript_stack_names_are_not_headline_identity(self):
        traces = [
            (b'Traceback (most recent call last):\n  File "old.py", line 1, in old\n    raise ValueError()\nValueError: invalid input\n',
             b'Traceback (most recent call last):\n  File "new.py", line 9, in new\n    raise ValueError()\nValueError: invalid input\n'),
            (b'TypeError: invalid input\n    at old (/a.js:1:2)\n',
             b'TypeError: invalid input\n    at new (/b.js:9:8)\n')]
        for first, second in traces:
            with self.subTest(first=first):
                self.assertEqual(normalize_stderr(first), normalize_stderr(second))
                self.assertNotEqual(normalize_stderr(first),
                                    normalize_stderr(second.replace(b"invalid input", b"different error")))
                self.assertEqual(normalize_stderr(first), normalize_stderr(normalize_stderr(first)))

    def test_rust_locations_and_backtraces_do_not_change_the_panic_message(self):
        first = b"thread 'main' panicked at old.rs:1:2:\nwriter failed\nstack backtrace:\n  0: old::helper\n"
        second = b"thread 'worker' panicked at new.rs:9:8:\nwriter failed\nnote: run with RUST_BACKTRACE=1\n"
        self.assertEqual(normalize_stderr(first), normalize_stderr(second))
        self.assertEqual(stderr_contract(first), stderr_contract(normalize_stderr(first)))
        self.assertNotEqual(normalize_stderr(first), normalize_stderr(second.replace(b"writer", b"reader")))

    def test_plain_bytes_and_non_utf8_diagnostics_remain_exact_except_addresses(self):
        for value in (b"", b"ordinary diagnostic\n", b"invalid byte \xff\n"):
            self.assertEqual(normalize_stderr(value), value)
        self.assertEqual(normalize_stderr(b"object at 0x12345678\n"), b"object at 0x_\n")
        invalid = b"panic: invalid byte \xff\n\ngoroutine 1 [running]:\nhelper()\n"
        literal = invalid.replace(b"\xff", b"\\xff")
        self.assertNotEqual(stderr_contract(invalid), stderr_contract(literal))
        json.dumps(stderr_contract(invalid), ensure_ascii=False).encode("utf-8")
        self.assertEqual(normalize_stderr(invalid), b"panic: invalid byte \xff\n")

    def test_run_case_retains_raw_stderr_and_normalizes_outside_the_clock(self):
        raw = StepResult(2, b"output", SECOND)
        module = SimpleNamespace(ENV={}, prepare=lambda *_: [],
                                 collect=lambda case, ctx, results: {case.id: results[0].stderr})
        executor = SimpleNamespace(run=lambda *_: [raw])
        with tempfile.TemporaryDirectory() as temporary:
            outputs, rows = run_case(module, Case("case", {}), SimpleNamespace(build=Path(temporary)), executor)
        self.assertEqual(outputs["case"], normalize_stderr(SECOND))
        self.assertEqual(raw.stderr, SECOND)
        self.assertEqual(base64.b64decode(rows[0].telemetry["raw_stderr_base64"]), SECOND)
        self.assertEqual(rows[0].telemetry["raw_stderr_sha256"], hashlib.sha256(SECOND).hexdigest())


class DifferentialStderrTests(unittest.TestCase):
    def test_opted_in_cli_comparison_ignores_frames_not_headline_changes(self):
        for enabled, expected in ((False, False), (True, True)):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temporary:
                task = Path(temporary) / "task"
                (task / "tests").mkdir(parents=True)
                settings = {"enabled": True, "seeds": [2718], "case_budget": 1,
                            "time_cap_seconds": 60}
                if enabled:
                    settings["stderr_contract"] = "cli-stderr-contract-v1"
                (task / "tests/judge.json").write_text(json.dumps({"differential": settings}))
                harness = SimpleNamespace(generate=lambda *_: [{}], shrink=lambda _: [])
                left = {"exit_code": 0, "result": [cli(FIRST)], "stderr": "", "leftovers": 0}
                right = {"exit_code": 0, "result": [cli(SECOND)], "stderr": "", "leftovers": 0}
                with patch.object(differential, "harness", return_value=harness), \
                        patch.object(differential, "execute", side_effect=[left, right]):
                    if expected:
                        result = differential.check(task, "base", "candidate", "image", "0", Path(temporary) / "out")
                        self.assertTrue(result["passed"])
                    else:
                        with self.assertRaises(differential.DifferentialMismatch):
                            differential.check(task, "base", "candidate", "image", "0", Path(temporary) / "out")
