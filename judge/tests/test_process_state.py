"""Offline real-child probe tests; Docker wiring is checked without a daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from judge import behavior, process_probe, process_state

FIXTURES = Path(__file__).parent / "fixtures/process_state"
NODE_KEYS = {"environment", "active_handles", "exec_flags", "heap_limit"}

# Frozen pre-hook probe, including its exact emitted field set and call sequence.
LEGACY_NODE = r'''const fs=require("node:fs"),v8=require("node:v8");
(async()=>{const m=await import("/app/build/project/main.js");await m.minify("function f(a){return a+1}f(2)");
const h=process._getActiveHandles().filter(x=>x!==process.stdin&&x!==process.stdout&&x!==process.stderr).map(x=>x.constructor.name).sort();
fs.writeFileSync("/scratch/process-state.json",JSON.stringify({environment:process.env,active_handles:h,exec_flags:process.execArgv.filter(x=>x!=="-e"),heap_limit:v8.getHeapStatistics().heap_size_limit}));})().catch(e=>{console.error(e);process.exit(1)});'''
LEGACY_BABEL_CALL = 'const m=require("/app/build/runtime/node_modules/@babel/core");m.transformSync("const x=1;", {configFile:false,babelrc:false});'


class ProcessStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = self.root / "new-task"
        self.tests = self.task / "tests"
        self.tests.mkdir(parents=True)
        self.build = self.root / "build"
        self.build.mkdir()
        for path in FIXTURES.iterdir():
            if path.is_file():
                shutil.copy(path, self.tests / path.name)
        shutil.copy(FIXTURES / "library.mjs", self.build / "library.mjs")
        shutil.copy(FIXTURES / "library.py", self.build / "library.py")
        self.settings = {"enabled": True, "kind": "node", "representative_operation": {
            "module": "/app/build/library.mjs", "script": "pack.mjs"}}
        self.configure()

    def configure(self):
        (self.tests / "judge.json").write_text(json.dumps({"process_state": self.settings}))
        shutil.copy(Path(process_probe.__file__).with_name("probe_lifecycle.py"),
                    self.root / "probe_lifecycle.py")

    def operation(self):
        return process_state.representative(self.task, self.build, self.settings)

    def run_node(self, program):
        if not shutil.which("node"):
            self.skipTest("Node is required for offline process-state execution tests")
        program = program.replace("/app/build", str(self.build)).replace(
            "/verifier-operation", str(self.tests)).replace("/scratch", str(self.root))
        script = self.root / "probe.cjs"
        script.write_text(program)
        subprocess.run(["node", str(script)], check=True, capture_output=True, timeout=10)
        return json.loads((self.root / "process-state.json").read_text())

    def test_terser_and_babel_outputs_identical_to_pre_hook_probe(self):
        project = self.build / "project"
        project.mkdir()
        (project / "main.js").write_text(
            'exports.minify=async text=>{if(text!=="function f(a){return a+1}f(2)")throw Error("input");'
            'process.env.REPRESENTATIVE_CALLED="minify";};')
        babel = self.build / "runtime/node_modules/@babel/core"
        babel.mkdir(parents=True)
        (babel / "index.js").write_text(
            'exports.transformSync=(text,opts)=>{if(text!=="const x=1;"||'
            'JSON.stringify(opts)!==JSON.stringify({configFile:false,babelrc:false}))throw Error("config");'
            'process.env.REPRESENTATIVE_CALLED="babel";};')
        for task, expected, legacy in [
            ("terser-minify", "minify", LEGACY_NODE),
            ("babel-project-compile", "babel", LEGACY_NODE.replace(
                process_state.NODE_CALL, LEGACY_BABEL_CALL)),
        ]:
            with self.subTest(task=task):
                self.assertEqual(process_state.node_program(task), legacy)
                previous = self.run_node(legacy)
                current = self.run_node(process_state.node_program(task))
                self.assertEqual(previous, current)
                self.assertEqual(set(current), NODE_KEYS)
                self.assertEqual(current["environment"]["REPRESENTATIVE_CALLED"], expected)

    def test_named_and_default_node_operations_run_before_unchanged_snapshot(self):
        for script, expected in [("pack.mjs", '{"hello":"world"}'), ("decompress.mjs", "2")]:
            with self.subTest(script=script):
                self.settings["representative_operation"]["script"] = script
                value = self.run_node(process_state.node_program(self.task.name, self.operation()))
                self.assertEqual(set(value), NODE_KEYS)
                self.assertEqual(value["environment"]["REPRESENTATIVE_CALLED"], expected)
                self.assertEqual(value["exec_flags"], [])
                self.assertEqual(value["active_handles"], [])
                self.assertGreater(value["heap_limit"], 0)
        self.assertNotIn("REPRESENTATIVE_CALLED", os.environ)

    def test_python_operation_runs_in_fresh_process_before_existing_snapshot(self):
        self.settings.update(kind="python", representative_operation={
            "module": "/app/build/library.py", "script": "operation.py"})
        operation = self.operation()
        # Same entrypoint and snapshot code as the verifier, with temporary mounts.
        source = Path(process_probe.__file__).read_text().replace(
            'Path("/scratch/process-state.json")', f'Path({str(self.root / "process-state.json")!r})')
        script = self.root / "probe.py"
        script.write_text(source)
        module = str(self.build / "library.py")
        subprocess.run([sys.executable, str(script), self.task.name, module,
                        str(self.tests / "operation.py")], check=True, timeout=10)
        value = json.loads((self.root / "process-state.json").read_text())
        self.assertEqual(set(value), set(process_probe.snapshot()))
        self.assertEqual(value["gc_thresholds"],
                         json.loads(value["environment"]["EXPECTED_GC_THRESHOLDS"]))
        self.assertEqual(value["gc_thresholds"][:2], [901, 11])
        self.assertTrue(value["gc_frozen"])
        self.assertEqual(value["environment"]["REPRESENTATIVE_CALLED"], "spawn")
        self.assertNotIn("REPRESENTATIVE_CALLED", os.environ)
        self.assertEqual(operation["script"], "/verifier-operation/operation.py")

    def run_python(self, source, task, *arguments):
        source = source.replace("/app/build/runtime", str(self.build)).replace(
            "/app/build/site", str(self.build)).replace("/scratch", str(self.root))
        script = self.root / "probe.py"
        script.write_text(source)
        shutil.rmtree(self.root / "sql", ignore_errors=True)
        subprocess.run([sys.executable, str(script), task, *arguments],
                       check=True, capture_output=True, timeout=10)
        return json.loads((self.root / "process-state.json").read_text())

    def test_python_package_uses_configured_project_root(self):
        project = self.build / "project"
        project.mkdir()
        package = project / "example_package"
        package.mkdir()
        shutil.copy(FIXTURES / "library.py", package / "__init__.py")
        self.settings.update(kind="python", representative_operation={
            "module": "example_package", "root": "/app/build/project", "script": "operation.py"})
        operation = self.operation()
        self.assertEqual(operation["root"], "/app/build/project")
        value = self.run_python(Path(process_probe.__file__).read_text(), self.task.name,
                                "example_package", str(self.tests / "operation.py"), str(project))
        self.assertEqual(value["environment"]["REPRESENTATIVE_CALLED"], "spawn")
        self.assertEqual(value["gc_thresholds"],
                         json.loads(value["environment"]["EXPECTED_GC_THRESHOLDS"]))
        self.assertEqual(value["gc_thresholds"][:2], [901, 11])
        self.assertTrue(value["gc_frozen"])

    def test_python_uses_configured_interpreter_and_build_root(self):
        project = self.build / "project"
        project.mkdir()
        (project / "example.py").write_text("value = 1\n")
        self.settings.update(kind="python", interpreter="/app/venv/bin/python",
                             representative_operation={"module": "example",
                                 "root": "/app/build/project", "script": "operation.py"})
        self.configure()
        def run(steps, ctx, environment):
            self.assertEqual(steps[0].argv, [
                "/app/venv/bin/python", "/verifier-process/process_probe.py", self.task.name,
                "example", "/verifier-operation/operation.py", "/app/build/project"])
            (ctx.scratch / "process-state.json").write_text("{}")
            return [SimpleNamespace(exit_code=0, stderr=b"", leftovers=[])]
        with patch.object(process_state, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = run
            process_state.probe(self.task, self.build, "fixture", "0")
        self.settings["representative_operation"]["module"] = "not_installed"
        self.assertEqual(self.operation()["status"], "not_configured")

    def test_unknown_python_task_no_longer_silently_snapshots(self):
        value = self.run_python(Path(process_probe.__file__).read_text(), "not-a-legacy-task")
        self.assertEqual(value["status"], "not_configured")
        self.assertNotIn("gc_thresholds", value)
        self.assertNotIn("environment", value)

    def test_all_legacy_python_outputs_match_except_frozen_count(self):
        modules = {
            "black.py": 'import os\nclass Mode: pass\ndef format_str(text, mode):\n assert text=="x=1\\n"\n os.environ["REPRESENTATIVE_CALLED"]="black"\n',
            "sqlglot.py": 'import os\ndef transpile(text, read, write):\n assert (text,read,write)==("SELECT 1 + 2","postgres","sqlite")\n os.environ["REPRESENTATIVE_CALLED"]="sqlglot"\n',
            "numpy.py": 'import os\ndef arange(n,dtype):\n assert n==100 and dtype is float\n return list(range(n))\ndef quantile(data,points):\n assert len(data)==100 and points==[0.25,0.5,0.75]\n os.environ["REPRESENTATIVE_CALLED"]="numpy"\n',
            "sqlfluff/__init__.py": "",
            "sqlfluff/core/__init__.py": 'import os\nfrom pathlib import Path\nclass FluffConfig:\n def __init__(self,overrides):\n  assert overrides=={"dialect":"ansi","processes":2}\nclass Linter:\n def __init__(self,config): pass\n def lint_paths(self,folders,processes):\n  assert processes==2\n  assert sorted(p.read_text() for p in Path(folders[0]).iterdir())==["SELECT 1;\\n"]*2\n  os.environ["REPRESENTATIVE_CALLED"]="sqlfluff"\n',
            "sqlfluff/core/linter/__init__.py": "",
            "sqlfluff/core/linter/runner.py": 'class Context:\n def Pool(self): pass\n def get_start_method(self): return "spawn"\nclass MultiProcessRunner:\n POOL_TYPE=Context().Pool\n',
        }
        for filename, source in modules.items():
            path = self.build / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        for task, expected in [
            ("black-project-format", "black"), ("sqlglot-mozilla-transpile", "sqlglot"),
            ("numpy-quantile", "numpy"), ("sqlfluff-project-lint", "sqlfluff"),
        ]:
            with self.subTest(task=task):
                legacy = self.run_python((FIXTURES / "legacy_python.py").read_text(), task)
                current = self.run_python(Path(process_probe.__file__).read_text(), task)
                self.assertIs(type(current["gc_frozen"]), int)
                self.assertEqual(current["gc_frozen"] > 0, legacy.pop("gc_frozen"))
                self.assertEqual({key: value for key, value in current.items() if key != "gc_frozen"}, legacy)
                self.assertEqual(current["environment"]["REPRESENTATIVE_CALLED"], expected)
                if task == "sqlfluff-project-lint":
                    self.assertEqual(current["pool_start_method"], "spawn")

    def test_missing_export_is_explicit_not_configured(self):
        self.settings["representative_operation"]["script"] = "missing.mjs"
        value = self.run_node(process_state.node_program(self.task.name, self.operation()))
        self.assertEqual(value["status"], "not_configured")
        self.assertNotIn("environment", value)

    def test_python_missing_export_is_explicit_not_configured(self):
        operation = self.tests / "missing.py"
        operation.write_text("not_an_operation = True\n")
        value = self.run_python(Path(process_probe.__file__).read_text(), "new-task",
                                str(self.build / "library.py"), str(operation))
        self.assertEqual(value["status"], "not_configured")
        self.assertNotIn("environment", value)

    def test_missing_and_unknown_operations_fail_closed_without_docker(self):
        variants = [
            {}, {"enabled": True, "kind": "unknown"},
            {"enabled": True, "kind": "node"},
            {"enabled": True, "kind": "python"},
            {"enabled": True, "kind": "node", "representative_operation": None},
            {"enabled": True, "kind": "node", "representative_operation": {}},
            {"enabled": True, "kind": "native", "representative_operation": {}},
        ]
        with patch.object(process_state, "DockerExecutor") as executor:
            for settings in variants:
                self.settings = settings
                self.configure()
                with self.subTest(settings=settings):
                    value = process_state.check(self.task, self.build, self.build, "unused", "0")
                    self.assertFalse(value["passed"])
                    self.assertEqual(value["status"], "not_configured")
                    self.assertFalse(value["claimable"])
                    self.assertIsNone(value["speedup"])
            executor.assert_not_called()

    def test_paths_must_be_existing_and_contained(self):
        for field, invalid in [
            ("script", "../library.mjs"), ("script", "/tmp/pack.mjs"),
            ("script", "missing-file.mjs"), ("script", "judge.json"),
            ("module", "/tmp/library.mjs"), ("module", "/app/build/../library.mjs"),
            ("module", "/app/build/missing.mjs"), ("module", 12),
        ]:
            with self.subTest(field=field, invalid=invalid):
                self.settings["representative_operation"] = {
                    "module": "/app/build/library.mjs", "script": "pack.mjs", field: invalid}
                self.assertEqual(self.operation()["status"], "not_configured")
        (self.tests / "escape.mjs").symlink_to(self.build / "library.mjs")
        self.settings["representative_operation"] = {
            "module": "/app/build/library.mjs", "script": "escape.mjs"}
        self.assertEqual(self.operation()["status"], "not_configured")
        outside = self.root / "outside.mjs"
        outside.write_text("export const value = 1;")
        (self.build / "escape.mjs").symlink_to(outside)
        self.settings["representative_operation"] = {
            "module": "/app/build/escape.mjs", "script": "pack.mjs"}
        self.assertEqual(self.operation()["status"], "not_configured")

    def test_read_only_verifier_mount_and_fresh_process_wiring(self):
        seen = []
        def run(steps, ctx, environment):
            seen.append((steps, ctx, environment))
            program = (ctx.scratch / "probe.cjs").read_text()
            self.assertIn("/verifier-operation/pack.mjs", program)
            self.assertNotIn("m.minify", program)
            (ctx.scratch / "process-state.json").write_text(json.dumps({
                "environment": {}, "active_handles": [], "exec_flags": [], "heap_limit": 123}))
            return [SimpleNamespace(exit_code=0, stderr=b"", leftovers=[])]
        with patch.object(process_state, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = run
            value = process_state.check(self.task, self.build, self.build, "fixture-image", "0")
            self.assertTrue(value["passed"])
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(seen[0][1].scratch, seen[1][1].scratch)
            for call in executor.call_args_list:
                self.assertIn(f"{self.tests.resolve()}:/verifier-operation:ro",
                              call.kwargs["extra_docker_flags"])
            for steps, _, _ in seen:
                self.assertEqual(steps[0].argv, ["node", "/scratch/probe.cjs"])
            self.assertEqual(value["baseline"]["rule_d_leftovers"], 0)

    def test_identical_unconfigured_results_never_pass(self):
        with patch.object(process_state, "probe", return_value={"status": "not_configured"}):
            result = process_state.check("task", "a", "b", "image", "0")
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "not_configured")

    def test_behavior_surfaces_disabled_probe_as_non_passing(self):
        self.settings["enabled"] = False
        self.configure()
        with patch.object(behavior.differential, "check", return_value={"passed": True}):
            result = behavior.check(self.task, self.build, self.build, "image", "0", self.root / "report")
        self.assertEqual(result["process_state"]["status"], "not_configured")
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["claimable"])
