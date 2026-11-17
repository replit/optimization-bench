import json
from pathlib import Path
import tempfile
import unittest

from judge import interfaces


CONFIG = {"language": "rust", "files": ["project/src/**/*.rs"]}


class RustProjectionTests(unittest.TestCase):
    def surface(self, root, code, *, dependency=None):
        project = Path(root) / "project"
        (project / "src").mkdir(parents=True, exist_ok=True)
        (project / "Cargo.toml").write_text(
            '[package]\nname="facade"\nversion="0.1.0"\n' +
            ('[dependencies]\ncore_lib={path="core"}\n' if dependency else ""))
        (project / "src/lib.rs").write_text(code)
        if dependency:
            (project / "core/src").mkdir(parents=True, exist_ok=True)
            (project / "core/Cargo.toml").write_text('[package]\nname="core-lib"\nversion="0.1.0"\n')
            (project / "core/src/lib.rs").write_text(dependency)
        return interfaces.declarations(root, CONFIG)

    def test_internal_tuple_visibility_widening_has_no_external_delta(self):
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, "pub struct JumpOffset(i32);")
            after = self.surface(root, "pub struct JumpOffset(pub(crate) i32);")
            self.assertTrue(interfaces.compare(before, after)["passed"])
            self.assertNotIn("facade::JumpOffset::0", before)
            self.assertNotIn("facade::JumpOffset::constructor", before)

    def test_public_fields_and_constructor_removal_are_breaking(self):
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, "pub struct Value(pub i32);")
            for code in ("pub struct Value(i32);", "pub struct Value(pub(crate) i32);",
                         "pub struct Value;"):
                with self.subTest(code=code):
                    self.assertFalse(interfaces.compare(before, self.surface(root, code))["passed"])

    def test_private_named_fields_and_private_type_methods_are_not_public(self):
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, "pub struct Value { pub x:u8, private:u16 } "
                                  "struct Internal; impl Internal { pub fn helper(){} }")
            after = self.surface(root, "pub struct Value { pub x:u8, pub(crate) internal:u32 } "
                                 "struct Internal; impl Internal { pub fn entirely_different(){} }")
            self.assertTrue(interfaces.compare(before, after)["passed"])
            self.assertFalse(any("Internal" in key for key in before))
            self.assertNotIn("facade::Value::private", before)
            self.assertNotIn("facade::Value::constructor", before)

    def test_transitive_workspace_reexports_protect_with_dict_method(self):
        dependency = ("mod hidden { pub struct Context; impl Context { "
                      "pub fn with_dict(mut dict: &[u8])->Self { Self } "
                      "pub(crate) fn private(){} } } pub use hidden::Context;")
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, "pub use core_lib::Context as Decoder;", dependency=dependency)
            self.assertIn("facade::Decoder::with_dict", before)
            self.assertNotIn("facade::Decoder::private", before)
            changed = dependency.replace("pub fn with_dict", "pub(crate) fn with_dict")
            after = self.surface(root, "pub use core_lib::Context as Decoder;", dependency=changed)
            report = interfaces.compare(before, after)
            self.assertFalse(report["passed"])
            self.assertIn("facade::Decoder::with_dict", report["removed"])

    def test_changed_signature_is_paired_old_to_new_not_unpaired_removal(self):
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, "pub fn process(value:u8)->u8{value}")
            after = self.surface(root, "pub fn process(renamed:u16)->u8{0}")
            report = interfaces.compare(before, after)
            self.assertFalse(report["passed"])
            self.assertEqual(report["removed"], [])
            self.assertEqual(report["changed"], ["facade::process"])
            self.assertEqual(report["changed_details"][0]["public_path"], "facade::process")
            self.assertIn("u8", report["changed_details"][0]["old"]["signature"])
            self.assertIn("u16", report["changed_details"][0]["new"]["signature"])

    def test_private_module_and_renamed_arguments_do_not_leak(self):
        code = ("mod implementation { pub struct Context; impl Context { "
                "pub fn read(&self, original: &[u8])->usize { 0 } } "
                "pub fn helper(){} } pub use implementation::Context;")
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            after = self.surface(root, code.replace("original", "renamed"))
            self.assertTrue(interfaces.compare(before, after)["passed"])
            self.assertIn("facade::Context::read", before)
            self.assertFalse(any("helper" in key for key in before))
            self.assertFalse(any("implementation" in key for key in before))

    def test_public_trait_and_public_enum_members_remain_protected(self):
        code = "pub trait Reader { fn read(&self, n:u8)->u8; } pub enum Kind { A, B(u8) }"
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            self.assertIn("facade::Reader::read", before)
            self.assertIn("facade::Kind::B", before)
            self.assertFalse(interfaces.compare(before, self.surface(root, code.replace(" B(u8)", "")))["passed"])

    def test_type_alias_identity_is_not_silently_split(self):
        code = "mod inner { pub struct Item; } pub use inner::Item as A; pub use inner::Item as B;"
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            after = self.surface(root, code.replace("pub use inner::Item as B;", "pub struct B;"))
            self.assertFalse(interfaces.compare(before, after)["passed"])

    def test_local_type_alias_exposes_methods_not_an_inaccessible_unit_constructor(self):
        code = ("mod hidden { pub struct Context; impl Context { pub fn with_dict(){} } } "
                "pub type Decoder = hidden::Context;")
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            self.assertIn("facade::Decoder::with_dict", before)
            self.assertNotIn("facade::Decoder::constructor", before)
            self.assertFalse(interfaces.compare(
                before, self.surface(root, code.replace("pub fn with_dict", "pub(crate) fn with_dict")))["passed"])

    def test_adding_a_public_alias_does_not_change_existing_type_signatures(self):
        code = "pub struct Context; pub fn echo(x:Context)->Context{x}"
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            after = self.surface(root, code + " pub use Context as EarlierAlias;")
            self.assertTrue(interfaces.compare(before, after)["passed"])

    def test_macro_matchers_not_private_expansion_bodies_are_contracts(self):
        code = "mod private { #[macro_export] macro_rules! build { ($x:expr) => { $x + 1 }; } }"
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            self.assertIn("facade::build", before)
            self.assertTrue(interfaces.compare(before, self.surface(root, code.replace("+ 1", "+ 2")))["passed"])
            self.assertFalse(interfaces.compare(before, self.surface(root, code.replace(":expr", ":ident")))["passed"])

    def test_type_projection_cannot_erase_a_public_string_constant_change(self):
        code = 'pub struct Context; pub const LABEL:&str="facade::Context";'
        with tempfile.TemporaryDirectory() as root:
            before = self.surface(root, code)
            after = self.surface(root, code.replace('"facade::Context"', '"different"'))
            self.assertFalse(interfaces.compare(before, after)["passed"])
