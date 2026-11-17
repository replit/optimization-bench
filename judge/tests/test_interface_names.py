"""Local bindings are not callable identity; types/defaults/public names still are."""
from pathlib import Path
import tempfile
import unittest

from judge import interfaces
from judge.c_preprocessor import preprocess, Unavailable


class InterfaceNameTests(unittest.TestCase):
    def declarations(self, root, language, raw=False):
        config = {"language": language, "files": ["api.*"]}
        def unavailable(*args):
            raise Unavailable("fixture exercises the protected raw-header fallback")
        return interfaces.declarations(root, config, preprocess=unavailable if raw else preprocess)

    def check_variants(self, language, suffix, source, compatible, incompatible):
        modes = [False, True] if language in ("c", "cpp") else [False]
        with tempfile.TemporaryDirectory() as temporary:
            baseline, candidate = [Path(temporary) / name for name in ("baseline", "candidate")]
            baseline.mkdir()
            candidate.mkdir()
            (baseline / ("api" + suffix)).write_text(source)
            for raw in modes:
                left = self.declarations(baseline, language, raw)
                for expected, variants in ((True, compatible), (False, incompatible)):
                    for changed in variants:
                        with self.subTest(language=language, raw=raw, source=changed):
                            (candidate / ("api" + suffix)).write_text(changed)
                            right = self.declarations(candidate, language, raw)
                            self.assertEqual(interfaces.compare(left, right)["passed"], expected)

    def test_go_parameter_receiver_result_names_grouping_and_formatting(self):
        source = ("package api\ntype paragraphParser struct{}\ntype Parser = paragraphParser\nfunc Keep() {}\n"
                  "func (b *paragraphParser) Open(_ ast.Node, reader text.Reader, _ Context) "
                  "(node ast.Node, state State) { return }\n")
        self.check_variants("go", ".go", source, [
            source.replace("_ Context", "pc Context"),
            source.replace("(b *paragraphParser)", "(parser *paragraphParser)")
                  .replace("node ast.Node, state State", "ast.Node, State")
                  .replace("reader text.Reader", "reader /* comment */ text.Reader"),
        ], [
            source.replace("_ Context", "_ OtherContext"),
            source.replace("_ ast.Node, reader text.Reader", "reader text.Reader, _ ast.Node"),
            source.replace("reader text.Reader, ", ""),
            source.replace("(node ast.Node, state State)", "(node ast.Node, state bool)"),
            source.replace("*paragraphParser", "paragraphParser"),
            source.replace("Open", "open"),
            "package api\nfunc Keep() {}\n",
        ])
        grouped = "package api\nfunc Keep() {}\nfunc Call(a, b int, cb func(x int) (y string)) {}\n"
        self.check_variants("go", ".go", grouped, [
            grouped.replace("a, b int", "first int, second int")
                   .replace("func(x int) (y string)", "func(int) string"),
        ], [grouped.replace("a, b int", "a int"), grouped.replace("y string", "y bool")])

    def test_rust_binding_names_and_mutability_not_type_or_method_identity(self):
        source = "pub fn keep() {}\npub fn call(mut input: i32, _: &str) -> bool { true }\n"
        self.check_variants("rust", ".rs", source, [
            source.replace("mut input", "value").replace("_: &str", "text : /* comment */ & str"),
        ], [
            source.replace("input: i32", "input: i64"),
            source.replace("mut input: i32, _: &str", "_: &str, mut input: i32"),
            source.replace(", _: &str", ""),
            source.replace("-> bool", "-> i32"),
            source.replace("pub fn call", "fn call"),
            "pub fn keep() {}\n",
        ])
        receiver = "pub struct API;\npub fn keep() {}\nimpl API { pub fn call(self: Box<Self>, value: i32) {} }\n"
        self.check_variants("rust", ".rs", receiver, [], [
            receiver.replace("self: Box<Self>", "value: Box<Self>"),
        ])

    def test_c_parameter_and_nested_callback_names_not_types(self):
        source = "int keep(void);\nint call(int count, void (*callback)(int value));\n"
        self.check_variants("c", ".h", source, [
            source.replace("count", "n").replace("callback", "cb").replace("value", "x"),
            "int keep(void); \nint call ( int, void (*)(int) );\n",
        ], [
            source.replace("int count", "long count"),
            source.replace("int value", "double value"),
            source.replace("int count, void (*callback)(int value)", "void (*callback)(int value), int count"),
            source.replace("int count, ", ""),
            source.replace("int call", "long call"),
            source.replace("int call", "int renamed"),
            "int keep(void);\n",
        ])

    def test_cpp_names_and_default_formatting_not_default_values(self):
        source = "int keep();\nint call(int value = 2, const char *message = \"hello world\");\n"
        self.check_variants("cpp", ".hpp", source, [
            source.replace("value = 2", "renamed= /* comment */ 2").replace("*message", "*text"),
        ], [
            source.replace("= 2", "= 3"),
            source.replace("int value", "long value"),
            source.replace("int value = 2, const char *message", "const char *message"),
            source.replace("int call", "long call"),
            source.replace("int call", "int renamed"),
            "int keep();\n",
        ])
        ordered = "int keep();\nint call(int count, const char *name);\n"
        self.check_variants("cpp", ".hpp", ordered, [], [
            ordered.replace("int count, const char *name", "const char *name, int count"),
        ])

    def test_java_parameter_names_and_formatting_not_types(self):
        source = "public class API { public int keep(){return 1;} public int call(int value){return value;} }"
        self.check_variants("java", ".java", source, [
            source.replace("int value", "int /* comment */ renamed").replace("return value", "return renamed"),
        ], [source.replace("int value", "long value"),
            source.replace("call(int value)", "call()"),
            source.replace("public int call", "public long call"),
            source.replace("call(int value)", "renamed(int value)"),
            source.replace("public int call(int value){return value;}", "")])
        ordered = "public class API {public int call(int count, String name){return count;}}"
        self.check_variants("java", ".java", ordered, [], [
            ordered.replace("int count, String name", "String name, int count"),
        ])
        spread = "public class API { public void call(String... args) {} }"
        self.check_variants("java", ".java", spread, [
            spread.replace("args", "renamed"),
        ], [spread.replace("String...", "String[]"), spread.replace("String...", "Object...")])

    def test_javascript_names_not_order_of_types_defaults_or_destructured_keys(self):
        signature = interfaces.javascript_signature
        self.assertTrue(interfaces.signature_compatible(signature("function f(a,b){}"),
                                                       signature("function f(b,a){}")))
        self.assertTrue(interfaces.signature_compatible(signature("function f(value = {flag: true}){}"),
                                                       signature("function f(renamed={ flag : /*ok*/ true }){}")))
        self.assertFalse(interfaces.signature_compatible(signature("function f({name: local}){}"),
                                                        signature("function f({other: local}){}")))
        self.assertFalse(interfaces.signature_compatible(signature("function f(value = 2){}"),
                                                        signature("function f(value = 3){}")))
        self.assertFalse(interfaces.signature_compatible(
            signature("function f(a: number, b: string){}", language="typescript"),
            signature("function f(b: string, a: number){}", language="typescript")))
        self.assertFalse(interfaces.signature_compatible(signature("function f(a,b){}"),
                                                        signature("function f(a){}")))
        self.assertFalse(interfaces.signature_compatible(
            signature("function f(a: number): string {return ''}", language="typescript"),
            signature("function f(a: number): number {return 1}", language="typescript")))
        self.assertFalse(interfaces.signature_compatible(signature("function f(a=1,b=2){}"),
                                                        signature("function f(a=2,b=1){}")))

    def test_python_keyword_names_and_strict_extension_contracts(self):
        compatible = interfaces.signature_compatible
        self.assertTrue(compatible("(value, /, *, flag = 1)", "(renamed, /, *, flag=1)"))
        self.assertFalse(compatible("(value=1)", "(renamed=1)"))
        self.assertFalse(compatible("(*, flag=1)", "(*, renamed=1)"))
        for changed in ("(value=2)", "(value=1, extra=2) -> str", "()"):
            self.assertFalse(compatible("(value=1) -> int", changed))
        self.assertFalse(compatible("(first, second)", "(second, first)"))
        for changed in ("(value: str = 1) -> int", "(value: int = 2) -> int",
                        "(value: int = 1) -> str", "(value: int = 1, extra=0) -> int"):
            old_typed = {"f": {"kind": "callable", "signature": "(value: int = 1) -> int",
                               "strict_signature": True}}
            new_typed = {"f": {"kind": "callable", "signature": changed, "strict_signature": True}}
            self.assertFalse(interfaces.compare(old_typed, new_typed)["passed"])
        self.assertFalse(compatible("(value)", "(value, required)"))
        old = {"f": {"kind": "callable", "signature": "(value, /, flag=1)", "strict_signature": True}}
        renamed = {"f": {"kind": "callable", "signature": "(renamed, /, flag = 1)", "strict_signature": True}}
        self.assertTrue(interfaces.compare(old, renamed)["passed"])
        extended = {"f": {"kind": "callable", "signature": "(value, /, flag=1, extra=2)", "strict_signature": True}}
        self.assertFalse(interfaces.compare(old, extended)["passed"])
