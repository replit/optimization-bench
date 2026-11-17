import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from judge import interfaces, policy, runtime_state
from judge.verifier import evaluate

FIXTURES = Path(__file__).parent / "fixtures"


class RuntimeRules(unittest.TestCase):
    def test_added_configuration_calls_across_languages_require_review(self):
        examples = {
            "parser.go": "debug.SetGCPercent(800)\n",
            "lib.py": "gc.disable()\nos.environ['GOGC'] = '800'\nsys.setrecursionlimit(10000)\n",
            "lib.js": "v8.setFlagsFromString('--expose_gc'); process.env.GOGC = '800';\n",
            "lib.c": "setenv(\"GOGC\", \"800\", 1); mallopt(M_ARENA_MAX, 1);\n",
            "lib.rs": "#[global_allocator]\nstatic A: MyAllocator = MyAllocator;\n",
        }
        self.assertEqual({r["path"] for r in runtime_state.findings({}, examples)}, set(examples))

    def test_unchanged_calls_comments_literals_and_environment_reads_are_not_hits(self):
        unchanged = {"lib.go": "debug.SetGCPercent(800)\n"}
        self.assertFalse(runtime_state.findings(unchanged, unchanged))
        text = {"lib.py": "# gc.disable()\nx = 'gc.disable()'\nx = os.environ['GOGC']\n",
                "lib.go": "// debug.SetGCPercent(800)\nx := \"debug.SetGCPercent(800)\"\n"}
        self.assertFalse(runtime_state.findings({}, text))

    def test_common_import_aliases_and_multiline_calls(self):
        text = {"lib.py": "from gc import disable as stop\nstop()\n",
                "lib.go": 'import rd "runtime/debug"\nrd.SetGCPercent(\n800)\n'}
        found = runtime_state.findings({}, text)
        self.assertEqual({r["path"] for r in found}, set(text))
        self.assertTrue(all(r["added_lines"] for r in found))

    def test_actual_sonnet_goldmark_patch_requires_review_and_has_no_ratio(self):
        patch_text = (FIXTURES / "sonnet55-goldmark.patch").read_text()
        added = "\n".join(line[1:] for line in patch_text.splitlines()
                          if line.startswith("+") and not line.startswith("+++"))
        hits = runtime_state.findings({}, {"parser/parser.go": added})
        self.assertTrue(any("SetGCPercent" in row["added_lines"][0]["text"] for row in hits))
        root = Path(__file__).resolve().parents[2] / "tasks/sqlglot-mozilla-transpile"
        # This isolates runtime-configuration review, not private-payload
        # authentication (covered separately, including pointer-only refusal).
        with tempfile.TemporaryDirectory() as temporary, \
             patch("judge.verifier.behavior.check", return_value={}), \
             patch("judge.verifier.heldout.check", return_value={"status": "unavailable"}), \
             patch("judge.verifier.telemetry.pinned", return_value=list(range(8))), \
             patch("judge.verifier.telemetry.fingerprint", return_value={}), \
             patch("judge.verifier.image_id", return_value="image"), \
             patch("judge.verifier.interfaces.check", return_value={"passed": True}):
            build = Path(temporary)
            (build / "fixture.txt").write_text("runtime-review unit fixture\n")
            result = evaluate(root, build, build, build, "image", "0-7", "test",
                              plumbing=True, dry_run=True, review_findings=hits)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["claimable"])
        self.assertIsNone(result["speedup"])
        self.assertEqual(result["runtime_configuration_review"], hits)


class PublicInterfaces(unittest.TestCase):
    def test_additions_are_compatible_but_signature_or_name_changes_are_not(self):
        old = {"m:f": {"kind": "callable", "signature": "(x, *, strict=True)"}}
        self.assertTrue(interfaces.compare(old, {**old, "m:extra": {"kind": "value"}})["passed"])
        self.assertFalse(interfaces.compare(old, {})["passed"])
        self.assertFalse(interfaces.compare(old, {"m:f": {"kind": "callable", "signature": "(x)"}})["passed"])

    @unittest.skipUnless(shutil.which("cc"), "C compiler required for the dynamic-symbol regression")
    def test_dynamic_symbols_detect_abi_break_even_when_both_sources_compile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, declaration in [("baseline", "int public_copy(void){return 1;}"),
                                      ("candidate", "static inline int public_copy(void){return 1;}")]:
                src = root / (name + ".c")
                src.write_text(declaration + "\nint other(void){return public_copy();}\n")
                subprocess.run(["cc", "-shared", "-fPIC", str(src), "-o", str(root / (name + ".so"))], check=True)
            result = interfaces.compare(interfaces.symbols(root / "baseline.so", True),
                                        interfaces.symbols(root / "candidate.so", True))
            self.assertFalse(result["passed"])
            self.assertIn("public_copy", result["removed"])

    def test_removed_header_declaration_is_not_hidden_by_a_new_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);(root / "api.h").write_text("extern int keep(int x);\n")
            config = {"language": "c", "files": ["api.h"]}
            before = interfaces.declarations(root, config)
            (root / "api.h").write_text("extern int replacement(int x);\n")
            self.assertFalse(interfaces.compare(before, interfaces.declarations(root, config))["passed"])
