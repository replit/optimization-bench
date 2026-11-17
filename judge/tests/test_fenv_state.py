"""Offline controls for review-only FP writes and actual native exit observation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import process_state, runtime_state
from judge.task_catalog import task_paths
from runner import Case, Step, StepResult

ROOT = Path(__file__).resolve().parents[2]


class FloatingPointReviewTests(unittest.TestCase):
    def test_c_and_cpp_global_writes_are_review_hits(self):
        calls = [
            "fesetround(FE_DOWNWARD)", "fesetenv(&env)", "feupdateenv(&env)",
            "feholdexcept(&env)", "feclearexcept(FE_ALL_EXCEPT)",
            "feraiseexcept(FE_INEXACT)", "fesetexceptflag(&flags, FE_ALL_EXCEPT)",
            "_MM_SET_ROUNDING_MODE(_MM_ROUND_DOWN)", "_mm_setcsr(0x8040)",
            "_MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON)",
            "_MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON)",
            "_controlfp(0, 0)", "_controlfp_s(&word, 0, 0)", "_control87(0, 0)",
        ]
        for suffix in ["c", "cpp", "h", "hpp"]:
            for call in calls:
                with self.subTest(suffix=suffix, call=call):
                    source = "void operation(void) {\n  " + call + ";\n}\n"
                    rows = runtime_state.findings({}, {"control." + suffix: source})
                    self.assertTrue(rows)
                    self.assertEqual(rows[0]["reason"], "process-global runtime configuration")
                    self.assertNotIn("passed", rows[0])  # Review evidence, never an automatic rejection.
                    self.assertEqual(runtime_state.findings({"control." + suffix: source},
                                                            {"control." + suffix: source}), [])

    def test_actual_assembly_and_rust_equivalents_are_reviewed(self):
        sources = [
            ("x.c", 'void f(){__asm__ volatile ("fldcw %0" : : "m"(cw));}'),
            ("x.cpp", 'void f(){asm volatile ("ldmxcsr %0" : : "m"(csr));}'),
            ("x.rs", 'fn f(){unsafe {core::arch::x86_64::_mm_setcsr(0x8040);}}'),
            ("x.rs", 'fn f(){unsafe {libc::fesetround(0);}}'),
            ("x.rs", 'fn f(){unsafe {core::arch::asm!("ldmxcsr [{0}]", in(reg) p);}}'),
            ("x.rs", 'fn f(){unsafe {std::arch::asm!(r#"fldcw [{0}]"#, in(reg) p);}}'),
            ("x.rs", '#[link_name = "llvm.x86.sse.ldmxcsr"]\nunsafe extern "C" {fn set_fp(p: *const u32);}'),
        ]
        for name, source in sources:
            with self.subTest(source=source):
                self.assertTrue(runtime_state.findings({}, {name: source}))

    def test_comments_and_ordinary_literals_are_not_calls_or_assembly(self):
        sources = {
            "x.c": '// fesetround(FE_DOWNWARD); asm("fldcw %0");\n'
                   '/* _mm_setcsr(0x8040); */\n'
                   'const char *s="fesetenv(x); fldcw ldmxcsr";\nvoid f(){fegetround();}\n',
            "x.cpp": 'const char* s=R"(fesetround(x); asm("ldmxcsr");)";\n',
            "x.rs": '// core::arch::asm!("fldcw");\n'
                    'const S: &str=r#"libc::fesetround(0); ldmxcsr"#;\n'
                    'const T: &str="llvm.x86.sse.ldmxcsr";\n'
                    '#[doc = r#"link_name = "llvm.x86.sse.ldmxcsr""#]\nfn f(){}\n',
            "x.go": 'package p\n// Go has no standard-library fenv setter.\n'
                    'const s="fesetround(0); fldcw"\n',
        }
        self.assertEqual(runtime_state.findings({}, sources), [])


@unittest.skipUnless(shutil.which("cc"), "Native observer controls require a C compiler")
class FloatingPointSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builds = {}
        source = '#include <fenv.h>\n#if defined(__SSE__)\n#include <xmmintrin.h>\n#endif\nint main(void){%s return 0;}\n'
        operations = {
            "original": "",
            "rounding": "fesetround(FE_DOWNWARD);",
            "flush": "#if defined(__SSE__)\n_mm_setcsr(_mm_getcsr() | 0x8040u);\n#endif\n",
            "sticky": "feraiseexcept(FE_INEXACT);",
        }
        for name, operation in operations.items():
            build = self.root / name
            build.mkdir()
            path = build / "program.c"
            # Preprocessor lines must start at a line boundary.
            path.write_text(source % ("\n" + operation + "\n"))
            subprocess.run(["cc", str(path), "-lm", "-o", str(build / "program")],
                           check=True, capture_output=True)
            self.builds[name] = build
        self.module = SimpleNamespace(
            ENV={}, cases=lambda inputs: [Case("fixture", {})],
            references=lambda inputs: [{"path": "fixture", "expect_len": 0,
                                       "expect_sha": hashlib.sha256(b"").hexdigest()}],
            prepare=lambda case, ctx: [Step([str(ctx.build / "program")])],
            collect=lambda case, ctx, results: {"fixture": results[0].stdout})

    def execute(self, steps, ctx, environment):
        # Exercise the real observer/child process; only Docker mount translation
        # and cgroup bookkeeping are replaced by this offline executor.
        results = []
        for step in steps:
            argv = [arg.replace("/verifier-process/", str(ROOT / "judge") + "/").replace(
                "/scratch/", str(ctx.scratch) + "/") for arg in step.argv]
            env = {"LC_ALL": "C", "PATH": os.environ.get("PATH", os.defpath)}
            env.update({key: value.replace("/scratch/", str(ctx.scratch) + "/")
                        for key, value in step.env.items()})
            result = subprocess.run(argv, cwd=ctx.scratch, env=env, capture_output=True, timeout=10)
            results.append(StepResult(result.returncode, result.stdout, result.stderr,
                                      telemetry={"execution_protocol": "process"}))
        file = ctx.scratch / "native-state.jsonl"
        if file.exists():
            # A container always sees the same /scratch paths in its environment.
            file.write_text(file.read_text().replace(str(ctx.scratch), "/scratch"))
        return results

    def check(self, task, candidate):
        dynamic_representative = process_state.dynamic_representative

        def fixture_dynamic(steps, ctx, settings):
            if settings.get("representative_dynamic"):
                # The adapter above substitutes our floating-point fixture for
                # the real library. Compile its dynamic counterpart too, rather
                # than trying to compile image-only bzip2 sources on the host.
                settings = {**settings, "representative_dynamic": {
                    "replace_executable": "program",
                    "compile": ["cc", str(ctx.build / "program.c"), "-lm",
                                "-o", "/scratch/state-target"]}}
            return dynamic_representative(steps, ctx, settings)

        with patch("judge.verifier.load_task", return_value=self.module), \
             patch.object(process_state, "dynamic_representative",
                          side_effect=fixture_dynamic), \
             patch.object(process_state, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = self.execute
            return process_state.check(task, self.builds["original"], self.builds[candidate],
                                       "offline-fixture", "0")

    def native_task(self):
        return ROOT / "tasks/sqlite-distinct-plant-facets"

    def test_fesetround_is_statically_reviewed_and_snapshot_difference_requires_review(self):
        self.assertTrue(runtime_state.findings({}, {
            "control.c": 'void f(){fesetround(FE_DOWNWARD);}'}))
        result = self.check(self.native_task(), "rounding")
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "needs_review")
        left = result["baseline"]["native_processes"][0]
        right = result["candidate"]["native_processes"][0]
        self.assertNotEqual(left["fp_rounding_mode"], right["fp_rounding_mode"])
        self.assertFalse(result["claimable"])
        self.assertIsNone(result["speedup"])

    def test_mxcsr_ftz_daz_is_statically_reviewed_and_observed(self):
        self.assertTrue(runtime_state.findings({}, {
            "control.c": 'void f(){_mm_setcsr(_mm_getcsr() | 0x8040u);}'}))
        result = self.check(self.native_task(), "flush")
        left = result["baseline"]["native_processes"][0]
        right = result["candidate"]["native_processes"][0]
        if left["mxcsr_control"] is None:
            self.skipTest("MXCSR controls are x86 SSE only")
        self.assertFalse(result["passed"])
        self.assertEqual(left["fp_rounding_mode"], right["fp_rounding_mode"])
        self.assertNotEqual(left["mxcsr_control"], right["mxcsr_control"])
        self.assertEqual(right["mxcsr_control"] & 0x8040, 0x8040)

    def test_sticky_fp_exception_flags_do_not_change_control_snapshot(self):
        self.assertTrue(self.check(self.native_task(), "sticky")["passed"])

    def test_all_catalog_task_configs_and_every_native_original_accept_offline_fixture(self):
        tasks = task_paths(ROOT)
        native = {task for task in tasks
                  if json.loads((task / "tests/judge.json").read_text())["process_state"]["kind"] == "native"}
        self.assertTrue(native, "Catalog must exercise native process-state checks")
        checked = set()
        for task in tasks:
            settings = json.loads((task / "tests/judge.json").read_text())
            if settings["process_state"]["kind"] != "native":
                continue
            with self.subTest(task=task.name):
                result = self.check(task, "original")
                self.assertTrue(result["passed"])
                row = result["baseline"]["native_processes"][0]
                self.assertIn("fp_rounding_mode", row)
                self.assertIn("mxcsr_control", row)
                checked.add(task)
        self.assertEqual(checked, native)
