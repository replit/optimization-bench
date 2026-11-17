"""Offline, pristine-relative positive and negative controls for every language."""
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import patch

from judge import interfaces, lexical, runtime_state

FIXTURE = Path(__file__).parent / "fixtures/serde-json-interface/ser.rs"


class LanguageInterfaceControls(unittest.TestCase):
    def setUp(self):
        # Unit fixtures use the real host compiler; production check() invokes
        # exactly the same preprocessor inside the task's isolated Docker image.
        from judge.c_preprocessor import preprocess
        self.addCleanup(patch.stopall)
        patch.object(interfaces, "docker_c_preprocess",
                     side_effect=lambda root, config, image, cpuset: preprocess(root, config)).start()

    def gate(self, language, suffix, original, variants):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, baseline, candidate = [root / p for p in ("task", "baseline", "candidate")]
            (task / "tests").mkdir(parents=True)
            baseline.mkdir()
            candidate.mkdir()
            config = {"kind": "declarations", "language": language, "files": ["*" + suffix]}
            (task / "tests/judge.json").write_text(json.dumps({"public_interface": config}))
            filename = ("API" if language == "java" else "api") + suffix
            (baseline / filename).write_text(original)
            path = candidate / filename
            path.write_text(original)
            self.assertTrue(interfaces.check(task, baseline, candidate, "unused", "0")["passed"])
            for variant in variants:
                with self.subTest(language=language, control=variant[:70]):
                    path.write_text(variant)
                    with self.assertRaises(interfaces.InterfaceBroken) as caught:
                        interfaces.check(task, baseline, candidate, "unused", "0")
                    self.assertFalse(caught.exception.report["passed"])

    def test_rust_lifetimes_visibility_raw_strings_and_nested_comments(self):
        source = '''
pub fn keep<'a>(x: &'a str) -> &'a str { x }
const RAW: &[u8] = br##"pub fn FAKE() { /* not comment */ }"##;
const BYTE: &[u8] = b"pub fn FAKE2() {}";
/* outer /* pub fn FAKE3() {} */ still comment */
pub fn to_writer<'a, T>(writer: &'a mut T, value: &'a str) {}
'''
        self.gate("rust", ".rs", source, [
            source.replace("pub fn to_writer", "fn to_writer"),
            source.replace("pub fn to_writer", "pub(crate) fn to_writer"),
        ])
        mask = lexical.code_only(source, ".rs")
        self.assertIn("keep<'a>", mask)
        self.assertIn("&'a str", mask)
        self.assertNotIn("FAKE", mask)

    def test_real_serde_json_to_writer_visibility_regression(self):
        source = FIXTURE.read_text()
        marker = "pub fn to_writer<"
        self.assertEqual(source.count(marker), 1)
        self.gate("rust", ".rs", source, [
            source.replace(marker, "fn to_writer<", 1),
            source.replace(marker, "pub(crate) fn to_writer<", 1),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ser.rs").write_text(source)
            surface = interfaces.declarations(root, {"language": "rust", "files": ["*.rs"]})
        matches = [key for key, row in surface.items()
                   if row.get("kind") == "function" and key.endswith("::to_writer")]
        self.assertEqual(len(matches), 1)
        self.assertNotIn("with_indent", matches[0])
        self.assertNotIn("pub fn to_writer_pretty", matches[0])

    def test_go_export_lowercased(self):
        source = '''package api
const note = `func FAKE() {}`
func Keep(x int) int { return x }
func Visible(x int) int { return x }
'''
        self.gate("go", ".go", source, [source.replace("func Visible", "func visible")])

    def test_js_and_ts_exports_removed_or_renamed(self):
        for language, suffix, annotation in [("javascript", ".js", ""), ("typescript", ".ts", ": number")]:
            source = (f"export function keep(x{annotation}) {{ return x; }}\n"
                      f"export function visible(x{annotation}) {{ return x; }}\n")
            self.gate(language, suffix, source, [
                source.replace("export function visible", "function visible"),
                source.replace("function visible", "function renamed"),
            ])

    def test_java_public_to_package_private(self):
        source = "public class API { public int keep() {return 1;} public int visible() {return 2;} }"
        self.gate("java", ".java", source, [
            source.replace("public int visible", "int visible"),
            source.replace("public class API", "class API"),
        ])

    def test_python_def_and_all_entry_removed(self):
        source = '__all__ = ["keep", "visible"]\ndef keep(x=1): return x\ndef visible(x=1): return x\n'
        removed = source.replace(', "visible"', "").replace("def visible(x=1): return x\n", "")
        self.gate("python", ".py", source, [removed])

    def test_c_and_cpp_header_prototypes_removed(self):
        for language, suffix, note in [
            ("c", ".h", 'const char *note = "int FAKE(void);";'),
            ("cpp", ".hpp", 'const char *note = R"tag(int FAKE(void); /* x */)tag";'),
        ]:
            source = note + "\nint keep(void);\nint visible(int x);\n"
            self.gate(language, suffix, source, [source.replace("int visible(int x);\n", "")])

    def test_literal_masking_preserves_offsets_and_executable_substitutions(self):
        examples = {
            ".rs": '''fn borrow<'a>(x: &'a str) {} let x='é'; let y=r###"HIDDEN"###; /* a /*HIDDEN*/ b */''',
            ".go": 'package x\nvar x=`HIDDEN`; var y="HIDDEN"; var z=\'é\' // HIDDEN\n',
            ".js": '''const q=8/2; const f=function(){} / 2;
const t=`HIDDEN ${/HIDDEN[\\/]/.test("HIDDEN")} ${process.env.X="HIDDEN"}`;
if(q) /HIDDEN/.test("HIDDEN"); // HIDDEN
''',
            ".ts": 'const f=()=>({x:2}); const t=`HIDDEN ${f().x / 2}`; /HIDDEN/.test("HIDDEN");',
            ".java": 'class X {String x="""\nHIDDEN /* comment markers */\n"""; char y=\'é\'; /* HIDDEN */}',
            ".py": 'import os\nx="""HIDDEN"""\ny=f"HIDDEN {os.putenv(\'HIDDEN\', \'HIDDEN\')}" # HIDDEN\n',
            ".c": 'char *x="HIDDEN"; char y=\'é\'; // HIDDEN\\\n HIDDEN\nint keep(void);',
            ".cpp": 'auto x=u8R"delim(HIDDEN "\\\\ /*HIDDEN*/)delim"; // HIDDEN\nint keep();',
        }
        for suffix, source in examples.items():
            with self.subTest(suffix=suffix):
                masked = lexical.code_only(source, suffix)
                self.assertEqual(len(source), len(masked))
                self.assertEqual([i for i, c in enumerate(source) if c == "\n"],
                                 [i for i, c in enumerate(masked) if c == "\n"])
                self.assertNotIn("HIDDEN", masked)
        self.assertIn("process.env.X", lexical.code_only(examples[".js"], ".js"))
        self.assertIn("os.putenv", lexical.code_only(examples[".py"], ".py"))
        self.assertTrue(runtime_state.findings({}, {"api.js": examples[".js"]}))

    def test_named_typescript_export_alias_removed(self):
        source = "const a = 1; export {a as visible}; export function keep() {}"
        self.gate("typescript", ".ts", source, [source.replace("a as visible", "a as renamed")])

    def test_public_signature_changes_in_every_language(self):
        controls = [
            ("rust", ".rs", "pub fn keep() {}\npub fn visible<T>(x:T)->T{x}",
             ["pub fn visible<T,U>", "pub fn visible<T>(x:T, y:T)", "pub fn visible<T>(x:T)->Option<T>"]),
            ("go", ".go", "package api\nfunc Keep() {}\nfunc Visible(x int) int {return x}",
             ["func Visible(x int, y int)", "func Visible(x string)"]),
            ("java", ".java", "public class API { public void keep() {} public int visible(int x) {return 1;} }",
             ["public int visible(int x, int y)", "public int visible(String x)"]),
            ("c", ".h", "int keep(void);\nint visible(int x);", ["int visible(long x)", "int visible(int x, int y)"]),
            ("cpp", ".hpp", "int keep();\nint visible(int x);", ["int visible(long x)"]),
            ("javascript", ".js", "export function keep(){}\nexport function visible(x){return x;}",
             ["function visible(x,y)"]),
            ("typescript", ".ts", "export function keep(){}\nexport function visible(x:number):number{return 1;}",
             ["function visible(x:string)", "function visible(x:number,y:number)"]),
        ]
        for language, suffix, source, replacements in controls:
            variants = []
            if language == "rust":
                old = "pub fn visible<T>(x:T)->T"
                variants = [source.replace("pub fn visible<T>", replacements[0]),
                            source.replace("pub fn visible<T>(x:T)", replacements[1]),
                            source.replace(old, replacements[2])]
            else:
                import re
                old = re.search(r"(?:public int visible|int visible|func Visible|function visible)\([^)]*\)", source)[0]
                variants = [source.replace(old, value) for value in replacements]
            self.gate(language, suffix, source, variants)
        python = '__all__=["keep","visible"]\ndef keep(): pass\ndef visible(x=1, *, flag=True): pass\n'
        self.gate("python", ".py", python, [
            python.replace("x=1", "x=2"),
            python.replace("x=1, *, flag", "x=1, flag"),
        ])

    def test_javascript_constructor_and_method_signatures_static(self):
        source = ("export class Packr { constructor(options = {}) {} pack(value) {return value;} }\n"
                  "export function keep() {}\n")
        self.gate("javascript", ".js", source, [
            source.replace("constructor(options = {})", "constructor()"),
            source.replace("pack(value)", "pack(value, options)"),
        ])
        self.assertTrue(interfaces.signature_compatible(
            interfaces.javascript_signature("function f(input, options = {}) {return input;}"),
            interfaces.javascript_signature("function renamed(value, cfg = {}) {return (value);}")))
        self.assertNotEqual(interfaces.javascript_signature("function f(input) {return '[native code]';}"),
                            interfaces.javascript_signature("function f() {return '[native code]';}"))
        typed = "export class Packr { constructor(options:number){} pack(value:number):number{return 1;} }"
        self.gate("typescript", ".ts", typed, [
            typed.replace("options:number", "options:string"),
            typed.replace("value:number", "value:string"),
        ])

    def test_overloaded_public_methods_and_functions_do_not_overwrite(self):
        java = "public class API {public int visible(int x){return 1;} public int visible(String x){return 2;}}"
        self.gate("java", ".java", java, [java.replace("public int visible(int x){return 1;}", "")])
        typescript = ("export function visible(x:number):number;\n"
                      "export function visible(x:string):string;\n"
                      "export function visible(x:any):any{return x;}\n")
        self.gate("typescript", ".ts", typescript, [
            typescript.replace("export function visible(x:number):number;\n", "")])

    def test_real_serde_runtime_setting_after_lifetimes_is_detected(self):
        source = FIXTURE.read_text()
        control = '\nfn process_state_control() { std::env::set_var("OPTIMBENCH_CONTROL", "changed"); }\n'
        alone = runtime_state.findings({}, {"control.rs": control})
        together = runtime_state.findings({"src/ser.rs": source}, {"src/ser.rs": source + control})
        self.assertTrue(alone)
        self.assertTrue(together)
        self.assertTrue(all(row["line"] > len(source.splitlines()) for row in together))
        self.assertEqual(runtime_state.findings({"src/ser.rs": source}, {"src/ser.rs": source}), [])

    def test_runtime_setting_controls_after_language_specific_literals(self):
        cases = {
            "control.rs": ("""fn keep<'a>(x: &'a str) {} const S:&str=r#"std::env::set_var()"#;\n""",
                           'fn change(){std::env::set_var("OPTIMBENCH_CONTROL","changed");}\n'),
            "control.go": ('package p\nimport "os"\nconst s=`os.Setenv("fake","fake")`\n',
                           'func change(){os.Setenv("OPTIMBENCH_CONTROL","changed")}\n'),
            "control.js": ('const a=/process.env.FAKE = 1/; const b=`process.env.FAKE=2 ${1 / 2}`;\n',
                           'process.env.OPTIMBENCH_CONTROL="changed";\n'),
            "control.ts": ('const a:string=`process.env.FAKE=2 ${1 / 2}`;\n',
                           'process.env.OPTIMBENCH_CONTROL="changed";\n'),
            "control.py": ('import os\ns=f"os.environ[\'fake\']=1 {1+2}"\n',
                           'os.environ["OPTIMBENCH_CONTROL"]="changed"\n'),
            "control.c": ('char *s="setenv(fake,fake,1)";\n',
                          'void change(void){setenv("OPTIMBENCH_CONTROL","changed",1);}\n'),
            "control.cpp": ('auto s=R"tag(setenv("fake","fake",1))tag";\n',
                            'void change(){setenv("OPTIMBENCH_CONTROL","changed",1);}\n'),
            "control.java": ('class C {String s="""\nSystem.setProperty("fake","fake")\n""";\n',
                             'void change(){System.setProperty("OPTIMBENCH_CONTROL","changed");}}\n'),
        }
        for name, (prefix, setter) in cases.items():
            with self.subTest(path=name):
                self.assertEqual(runtime_state.findings({}, {name: prefix}), [])
                self.assertTrue(runtime_state.findings({name: prefix}, {name: prefix + setter}))
                self.assertEqual(runtime_state.findings({name: prefix + setter},
                                                       {name: prefix + setter}), [])

    @unittest.skipUnless(shutil.which("node"), "Node executable required for offline runtime probe control")
    def test_javascript_runtime_constructor_arity_and_prototype_method_control(self):
        source = ("class Unpackr {constructor(options) {} unpack(data) {return data;}"
                  "get size(){throw new Error('accessor must not execute');} set size(value){} static make(value){return value;}}\n"
                  "class Packr extends Unpackr {constructor(options = {}) {super(options);} pack(value) {return value;}}\n"
                  "module.exports = {Packr, Unpackr};\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task, baseline, candidate = [root / p for p in ("task", "baseline", "candidate")]
            (task / "tests").mkdir(parents=True)
            baseline.mkdir()
            candidate.mkdir()
            config = {"kind": "javascript", "entries": [{"mode": "require", "path": "api.cjs"}]}
            (task / "tests/judge.json").write_text(json.dumps({"public_interface": config}))
            (baseline / "api.cjs").write_text(source)
            def host_probe(build, config, image, cpuset):
                with tempfile.TemporaryDirectory() as scratch:
                    cfg, output = Path(scratch) / "config.json", Path(scratch) / "out.json"
                    cfg.write_text(json.dumps(config))
                    subprocess.run(["node", str(interfaces.HERE / "javascript_interface_probe.cjs"),
                                    str(cfg), str(build), str(output)], check=True, capture_output=True)
                    return interfaces.javascript_signatures(json.loads(output.read_text()))
            with patch.object(interfaces, "docker_surface", side_effect=host_probe):
                for changed in [source,
                                source.replace("return value;", "return (value);")]:
                    (candidate / "api.cjs").write_text(changed)
                    self.assertTrue(interfaces.check(task, baseline, candidate, "unused", "0")["passed"])
                for changed in [source.replace("constructor(options)", "constructor(options, extra)"),
                                source.replace("constructor(options)", "constructor()"),
                                source.replace("constructor(options = {})", "constructor()"),
                                source.replace("pack(value)", "pack(value, options)")]:
                    (candidate / "api.cjs").write_text(changed)
                    with self.assertRaises(interfaces.InterfaceBroken):
                        interfaces.check(task, baseline, candidate, "unused", "0")
                surface = host_probe(baseline, config, "unused", "0")
                root = ["api.cjs", "require", "exports", "Unpackr", "value"]
                constructor = interfaces.javascript_graph_lookup(surface, root)[0]
                self.assertEqual(constructor["length"], 1)
                accessor = interfaces.javascript_graph_lookup(surface, root + ["own", "prototype", "own", "size"])[0]
                self.assertEqual(accessor["kind"], "accessor")
                self.assertTrue(accessor["get"])
                self.assertTrue(accessor["set"])
                self.assertEqual(interfaces.javascript_graph_lookup(surface, root + ["own", "make"])[0]["length"], 1)
                self.assertFalse(interfaces.compare(
                    {"Packr": {"kind": "function", "length": 1}},
                    {"Packr": {"kind": "function", "length": 2}})["passed"])
