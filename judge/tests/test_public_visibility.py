from pathlib import Path
import tempfile
import unittest

from judge import interfaces


class PublicVisibilityControls(unittest.TestCase):
    def surface(self, language, code):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suffix = {"go": ".go", "python": ".py", "typescript": ".ts",
                      "javascript": ".js", "java": ".java"}[language]
            (root / ("API" + suffix)).write_text(code)
            return interfaces.declarations(root, {"language": language, "files": ["*" + suffix]})

    def test_go_unexported_details_do_not_hide_exported_fields_or_methods(self):
        code = ("package api\ntype Public struct { Value int; internal int }; "
                "func (p *Public) Run(value int) int{return value}; "
                "func (p *Public) helper(){}")
        before = self.surface("go", code)
        changed_private = code.replace("internal int", "private string").replace("helper()", "otherHelper()")
        self.assertTrue(interfaces.compare(before, self.surface("go", changed_private))["passed"])
        for changed in (code.replace("Value int", "value int"), code.replace("Run", "run"),
                        code.replace("Value int", "Value string")):
            with self.subTest(changed=changed):
                report = interfaces.compare(before, self.surface("go", changed))
                self.assertFalse(report["passed"])
        report = interfaces.compare(before, self.surface("go", code.replace("Value int", "Value string")))
        self.assertEqual(report["removed"], [])
        self.assertTrue(any(row["public_path"].endswith(".Value") for row in report["changed_details"]))

    def test_go_local_alias_and_private_embedding_promote_public_methods(self):
        code = ("package api\ntype hidden struct{ X int }; "
                "func (p *hidden) Run(value int)int{return value}; "
                "type Public = hidden; type Embedded struct{ *hidden }")
        before = self.surface("go", code)
        self.assertTrue(any(key.endswith(":Public.Run") for key in before))
        self.assertTrue(any(key.endswith(":Embedded.Run") for key in before))
        after = self.surface("go", code.replace("Run", "run"))
        self.assertFalse(interfaces.compare(before, after)["passed"])

    def test_go_import_aliases_and_argument_bindings_are_not_public_types(self):
        code = 'package api\nimport renamed "io"\nfunc Read(value renamed.Reader) error{return nil}'
        before = self.surface("go", code)
        after = self.surface("go", code.replace("renamed", "another").replace("value", "input"))
        self.assertTrue(interfaces.compare(before, after)["passed"])

    def test_python_all_and_class_visibility(self):
        code = ("__all__=['API']\n"
                "class API:\n value:int=1\n _internal:int=2\n def run(self, value): return value\n"
                "def unadvertised(): pass\n")
        before = self.surface("python", code)
        after = self.surface("python", code.replace("_internal", "_private").replace("unadvertised", "another"))
        self.assertTrue(interfaces.compare(before, after)["passed"])
        self.assertFalse(interfaces.compare(before, self.surface("python", code.replace(" value:int", " _value:int")))["passed"])
        self.assertFalse(any("unadvertised" in key for key in before))

    def test_javascript_private_fields_are_not_public_fields(self):
        code = "export class API { #internal=1; Value=2; run(value){ return value; } }"
        before = self.surface("javascript", code)
        self.assertTrue(interfaces.compare(before, self.surface(
            "javascript", code.replace("#internal=1", "#changed=99")))["passed"])
        self.assertFalse(interfaces.compare(before, self.surface(
            "javascript", code.replace("Value=2", "#Value=2")))["passed"])

    def test_typescript_private_widening_and_public_field_narrowing(self):
        code = ("export class API { private storage:number=1; public Value:number=2; "
                "protected extend(value:number){} public run(value:number){} }")
        before = self.surface("typescript", code)
        after = self.surface("typescript", code.replace("private storage", "protected storage"))
        self.assertTrue(interfaces.compare(before, after)["passed"])
        for changed in (code.replace("public Value", "private Value"),
                        code.replace("protected extend", "private extend"),
                        code.replace("public Value", "public readonly Value"),
                        code.replace("public run", "protected run")):
            with self.subTest(changed=changed):
                self.assertFalse(interfaces.compare(before, self.surface("typescript", changed))["passed"])

    def test_typescript_private_constructor_bindings_are_not_public(self):
        code = "export class API { private constructor(hidden:number){} public static make(){} }"
        before = self.surface("typescript", code)
        after = self.surface("typescript", code.replace("hidden:number", "internal:string, extra:boolean"))
        self.assertTrue(interfaces.compare(before, after)["passed"])
        public = self.surface("typescript", code.replace("private constructor", "public constructor"))
        self.assertFalse(interfaces.compare(public, before)["passed"])

    def test_java_non_public_owner_and_fields_do_not_leak(self):
        code = ("public class API { public int Value; private int internal; "
                "private class Hidden { public void accidental(){} } "
                "public int run(int value){return value;} }")
        before = self.surface("java", code)
        changed = code.replace("private int internal", "int otherInternal").replace("accidental", "different")
        self.assertTrue(interfaces.compare(before, self.surface("java", changed))["passed"])
        self.assertFalse(interfaces.compare(before, self.surface("java", code.replace("public int Value", "private int Value")))["passed"])
        after = self.surface("java", code.replace("run(int value)", "run(long renamed)"))
        report = interfaces.compare(before, after)
        self.assertFalse(report["passed"])
        self.assertEqual(report["removed"], [])
        self.assertTrue(any(":run" in row["public_path"] for row in report["changed_details"]))

    def test_java_explicit_default_constructor_is_not_an_api_change(self):
        before = self.surface("java", "public class API {public void run(){}}")
        after = self.surface("java", "public class API {public API(){} public void run(){}}")
        self.assertTrue(interfaces.compare(before, after)["passed"])

    def test_c_changed_signature_is_paired_and_inline_qualification_stays_checked(self):
        before = {":int run ( int ) ;": True}
        after = {":long run ( int ) ;": True}
        report = interfaces.compare(before, after)
        self.assertFalse(report["passed"])
        self.assertEqual(report["removed"], [])
        self.assertEqual(len(report["changed_details"]), 1)
        self.assertFalse(interfaces.compare(before, {":inline int run ( int ) ;": True})["passed"])
