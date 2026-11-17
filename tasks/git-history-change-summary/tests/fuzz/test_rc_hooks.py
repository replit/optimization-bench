"""Focused registration/domain tests. Native smoke replay is documented in README."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
NAME = "_git_rc_test_package"
package = types.ModuleType(NAME)
package.__path__ = [str(HERE)]
sys.modules[NAME] = package
spec = importlib.util.spec_from_file_location(NAME + ".rc_hooks", HERE / "rc_hooks.py")
hooks = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hooks
spec.loader.exec_module(hooks)
driver = sys.modules[NAME + ".rc_driver"]


class RegistrationTests(unittest.TestCase):
    def test_only_three_allowed_fact_hooks(self):
        self.assertEqual(set(hooks.GATES), {"stream_faults", "repeat_in_process", "aliasing"})
        self.assertTrue(all(callable(v) for v in hooks.GATES.values()))

    def test_all_fixed_cases_are_valid(self):
        self.assertEqual(len(driver.cases()), 11)
        for case in driver.cases():
            self.assertTrue(hooks.PRECONDITION(case, driver.SURFACE)["valid"])

    def test_unsupported_private_or_malformed_case_is_rejected(self):
        case = driver.cases()[0]
        for delta in (
            {"surface_id": "git.private"},
            {"task": "another-task"},
            {"options": {"scenario": True, "flags": 0, "fail_callback": 0}},
            {"options": {"scenario": 4, "flags": 0, "fail_callback": 0}},
            {"input": {"fixture": "unreviewed"}},
        ):
            self.assertFalse(hooks.PRECONDITION({**case, **delta}, driver.SURFACE)["valid"])

    def test_exact_output_and_native_affinity_are_preserved(self):
        case = driver.cases()[0]
        row = {"return_code": 0, "second_return_code": 0, "pid": 123,
               "output_hex": "0061ff", "inputs_unchanged": True,
               "retained_output_unchanged": True, "execution_affinity": [2, 3]}
        observation = driver.observe(row, case, "a" * 64)
        self.assertEqual(observation["effects"]["output_hex"], "0061ff")
        self.assertEqual(observation["diagnostics"]["execution_affinity"], [2, 3])
        self.assertEqual(observation["identity"]["build_sha256"], "a" * 64)

    def test_callback_failure_is_typed_not_message_normalized(self):
        case = driver.cases()[7]
        row = {"return_code": -1, "second_return_code": 0, "pid": 123,
               "output_hex": "", "inputs_unchanged": True,
               "retained_output_unchanged": True, "execution_affinity": [0]}
        observation = driver.observe(row, case, "a" * 64)
        self.assertEqual(observation["outcome"], {
            "kind": "error", "code": -1, "category": "xdiff-callback-aborted"})

    def test_full_generic_registration_is_present(self):
        from judge.fuzzing_generic import task_runners
        from tools.release_preflight import generic_registrations
        task = HERE.parents[1]
        callbacks, module = task_runners(task)
        self.assertEqual(set(callbacks), set(hooks.GATES))
        self.assertTrue(callable(module.CLI_CASES))
        self.assertTrue(callable(module.HEADER_CONSUMERS))
        self.assertEqual(generic_registrations(task)["status"], "registered")

    def test_source_bound_two_tu_header_matrix(self):
        from tools.fuzz_runners.header_matrix import validate_manifest
        manifest = json.loads(Path(hooks.HEADER_CONSUMERS({})["manifest"]).read_text())
        validate_manifest(manifest)
        self.assertEqual({c["language"] for c in manifest["cells"]}, {"c", "c++"})
        for cell in manifest["cells"]:
            self.assertTrue(cell["supported"])
            self.assertEqual(len(cell["translation_units"]), 2)
            self.assertIn("project/libgit.a", cell["libraries"])

    @unittest.skipUnless(os.environ.get("GIT_HEADER_SOURCE"), "Authenticated upstream source not supplied")
    def test_header_evidence_contains_literal_quotes_in_pinned_source(self):
        root = Path(os.environ["GIT_HEADER_SOURCE"])
        manifest = json.loads(Path(hooks.HEADER_CONSUMERS({})["manifest"]).read_text())
        for cell in manifest["cells"]:
            for ref in cell["support_refs"]:
                content = (root / ref["path"].removeprefix("project/")).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), ref["sha256"])
                self.assertIn(ref["quote"], content.decode())

    @unittest.skipUnless(os.environ.get("GIT_HEADER_SOURCE") and shutil.which("cc") and shutil.which("c++"),
                         "Authenticated source and C/C++ compilers required")
    def test_real_header_consumer_syntax_without_claiming_task_image_linkage(self):
        self.test_header_evidence_contains_literal_quotes_in_pinned_source()
        root = Path(os.environ["GIT_HEADER_SOURCE"])
        manifest = json.loads(Path(hooks.HEADER_CONSUMERS({})["manifest"]).read_text())
        with tempfile.TemporaryDirectory() as tmp:
            for cell in manifest["cells"]:
                for unit in cell["translation_units"]:
                    source = Path(tmp) / unit["name"]
                    source.write_text(unit["source"])
                    subprocess.run([cell["compiler"], "-fsyntax-only", "-I", str(root), str(source)],
                                   check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("git"), "Trusted Git required for CLI fixture preparation")
    def test_cli_fixtures_have_successful_small_and_large_stdout(self):
        # This tests request/fixture construction with the host Git, not native
        # qualification of the task image or the shared IO fault matrix.
        with tempfile.TemporaryDirectory() as tmp:
            request = hooks.CLI_CASES({"out": tmp})
            self.assertEqual(len(request["cases"]), 2)
            # No admitted case has output_dirs, so the generic CLI gate needs
            # this declaration instead of readonly_output credit.
            self.assertTrue(all(not spec.get("output_dirs") for spec in request["cases"]))
            self.assertTrue(request["readonly_applicability_evidence"]["scope"])
            for spec in request["cases"]:
                self.assertTrue(hooks.CLI_PRECONDITION(spec["case"], spec["surface"])["valid"])
                # The task build reads system config/attributes from /root/etc,
                # which the non-root cli_faults worker cannot read.
                self.assertEqual(spec["step"]["env"],
                                 {"GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1"})
                argv = ["git", *spec["step"]["argv"][1:]]
                result = subprocess.run(argv, cwd=spec["inputs"], capture_output=True, check=True,
                                        env={**os.environ, **spec["step"]["env"]})
                if spec["payload_class"] == "small":
                    self.assertTrue(0 < len(result.stdout) <= 4096)
                else:
                    self.assertGreaterEqual(len(result.stdout), 65536)
                changed = {**spec["case"], "options": {"algorithm": "unreviewed"}}
                self.assertFalse(hooks.CLI_PRECONDITION(changed, spec["surface"])["valid"])


if __name__ == "__main__":
    unittest.main()
