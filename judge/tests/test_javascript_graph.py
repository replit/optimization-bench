"""Bounded alias/cycle regression, including the full original msgpackr exports."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from judge import interfaces

HERE = Path(__file__).parent
PROBE = HERE.parent / "javascript_interface_probe.cjs"


class JavascriptGraphTests(unittest.TestCase):
    def probe(self, root, config):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the verifier runtime probe")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "config.json").write_text(json.dumps(config))
            subprocess.run([node, str(PROBE), str(folder / "config.json"), str(root),
                            str(folder / "surface.json")], check=True, capture_output=True, timeout=15)
            return json.loads((folder / "surface.json").read_text())

    def fixture(self, source, config=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "api.cjs").write_text(source)
        return root, config or {"entries": [{"path": "api.cjs", "mode": "require"}]}

    def test_full_original_msgpackr_esm_and_cjs_exports(self):
        root = HERE / "fixtures/msgpackr-interface"
        config = {"entries": [{"path": "project/node-index.js", "mode": "import"},
                              {"path": "project/dist/node.cjs", "mode": "require"}]}
        surface = self.probe(root, config)
        self.assertFalse(surface["$meta"]["truncated"])
        self.assertLess(surface["$meta"]["nodes"], 1000)
        self.assertLess(len(json.dumps(surface)), 250000)
        self.assertGreater(surface["$meta"]["aliases"], 20)
        self.assertFalse(any("callable_source" in row for row in surface.values()))
        normalized = interfaces.javascript_signatures(surface)
        self.assertTrue(interfaces.compare(normalized, normalized)["passed"])
        for entry in config["entries"]:
            for name in ["Packr", "Unpackr", "PackrStream", "UnpackrStream", "EncoderStream", "DecoderStream"]:
                row, _ = interfaces.javascript_graph_lookup(
                    normalized, [entry["path"], entry["mode"], "exports", name, "value"])
                self.assertEqual(row["kind"], "function", name)
                self.assertIn("signature", row)
                self.assertIn("source_sha256", row)

    def test_aliased_stream_subclasses_cycles_and_static_diamonds(self):
        source = """
const {Transform}=require('node:stream');
class Decoder extends Transform { constructor(options){super(options)}
  read(value){return value} get size(){throw Error('getter must not execute')} }
class Encoder extends Transform { constructor(options){super(options)} write(value){return value} }
Decoder.other=Encoder; Encoder.other=Decoder; Decoder.self=Decoder;
module.exports={Decoder,DecoderAlias:Decoder,Encoder,EncoderAlias:Encoder};
"""
        root, config = self.fixture(source)
        left = self.probe(root, config)
        self.assertFalse(left["$meta"]["truncated"])
        self.assertLess(left["$meta"]["nodes"], 100)
        self.assertGreater(left["$meta"]["aliases"], 3)
        self.assertTrue(any(row.get("builtin", "").startswith("node:stream.Transform")
                            for row in left.values()))
        self.assertEqual(left, self.probe(root, config))
        normalized = interfaces.javascript_signatures(left)
        self.assertTrue(interfaces.compare(normalized, normalized)["passed"])
        for change in [source.replace("constructor(options)", "constructor(options,extra)", 1),
                       source.replace("read(value)", "read(value,options)"),
                       source.replace("DecoderAlias:Decoder,", "")]:
            (root / "api.cjs").write_text(change)
            candidate = interfaces.javascript_signatures(self.probe(root, config))
            self.assertFalse(interfaces.compare(normalized, candidate)["passed"])

    def test_added_alias_can_change_canonical_path_without_losing_members(self):
        source = "const a={call(value){return value}};a.self=a;module.exports={first:a,second:a};"
        root, config = self.fixture(source)
        left = interfaces.javascript_signatures(self.probe(root, config))
        (root / "api.cjs").write_text(source + "module.exports.aaa=module.exports.first;")
        right = interfaces.javascript_signatures(self.probe(root, config))
        self.assertTrue(interfaces.compare(left, right)["passed"])
        (root / "api.cjs").write_text(source + "delete a.call;")
        right = interfaces.javascript_signatures(self.probe(root, config))
        self.assertFalse(interfaces.compare(left, right)["passed"])

    def test_alias_identity_relation_cannot_silently_break(self):
        source = "function make(){return {call(value){return value}}}const a=make();module.exports={a,b:a};"
        root, config = self.fixture(source)
        left = interfaces.javascript_signatures(self.probe(root, config))
        (root / "api.cjs").write_text(source + "module.exports.b=make();")
        right = interfaces.javascript_signatures(self.probe(root, config))
        self.assertFalse(interfaces.compare(left, right)["passed"])

    def test_depth_and_node_limits_are_explicit_and_fail_closed_even_when_equal(self):
        for limits in [{"max_depth": 0, "max_nodes": 100}, {"max_depth": 12, "max_nodes": 3}]:
            root, config = self.fixture("module.exports={a:{b:{call(value){return value}}}}")
            config["graph_limits"] = limits
            surface = self.probe(root, config)
            self.assertTrue(surface["$meta"]["truncated"])
            self.assertEqual(surface, self.probe(root, config))
            self.assertLessEqual(surface["$meta"]["nodes"], limits["max_nodes"])
            self.assertFalse(interfaces.compare(surface, surface)["passed"])
            with self.assertRaisesRegex(ValueError, "Incomplete JavaScript public surface"):
                interfaces.javascript_signatures(surface)

    def test_public_backward_compatible_additions_and_internal_changes(self):
        source = """
class Public {constructor(options){this.options=options}
  call(value){return value} _internal(value){return value} }
module.exports={Public,call(value){return value}};
"""
        root, config = self.fixture(source)
        baseline = interfaces.javascript_signatures(self.probe(root, config))
        compatible = [
            source.replace("constructor(options)", "constructor(options, extra={})"),
            source.replace("constructor(options)", "constructor(options={})"),
            source.replace("call(value)", "call(value,...extra)"),
            source.replace("_internal(value){return value}", "_renamed(extra,more){return extra}"),
            source.replace("return value", "return (value)"),
            source + "module.exports.added = function(options={}){};",
        ]
        for changed in compatible:
            (root / "api.cjs").write_text(changed)
            candidate = interfaces.javascript_signatures(self.probe(root, config))
            self.assertTrue(interfaces.compare(baseline, candidate)["passed"], changed)
        for changed in [source.replace("constructor(options)", "constructor(options,extra)"),
                        source.replace("call(value){return value}", ""),
                        source.replace("module.exports={Public,", "module.exports={Renamed:Public,")]:
            (root / "api.cjs").write_text(changed)
            candidate = interfaces.javascript_signatures(self.probe(root, config))
            self.assertFalse(interfaces.compare(baseline, candidate)["passed"], changed)

    def test_nonexported_bases_are_not_public_but_exported_base_members_are(self):
        source = "class Base{method(value){return value}}class Public extends Base{}module.exports={Public};"
        root, config = self.fixture(source)
        baseline = interfaces.javascript_signatures(self.probe(root, config))
        (root / "api.cjs").write_text(source.replace("method(value)", "method(value,extra)"))
        self.assertTrue(interfaces.compare(baseline, interfaces.javascript_signatures(self.probe(root, config)))["passed"])
        (root / "api.cjs").write_text(source + "module.exports.Base=Base;")
        exposed = interfaces.javascript_signatures(self.probe(root, config))
        (root / "api.cjs").write_text(source.replace("method(value)", "method(value,extra)") + "module.exports.Base=Base;")
        self.assertFalse(interfaces.compare(exposed, interfaces.javascript_signatures(self.probe(root, config)))["passed"])

    def test_reordered_local_parameter_names_are_not_interface_identity(self):
        root, config = self.fixture("module.exports={call(first,second){return first}}")
        baseline = interfaces.javascript_signatures(self.probe(root, config))
        (root / "api.cjs").write_text("module.exports={call(second,first){return first}}")
        self.assertTrue(interfaces.compare(baseline, interfaces.javascript_signatures(self.probe(root, config)))["passed"])

    def test_python_and_typed_source_optional_additions(self):
        python = interfaces.structured_declarations("def call(value: str): return value\n", "python", "api.py")
        for changed in ["def call(value: str, extra=None): return value\n",
                        "def call(value: str, *, option=None): return value\n",
                        "def call(value: str = ''): return value\n"]:
            self.assertTrue(interfaces.compare(python, interfaces.structured_declarations(changed, "python", "api.py"))["passed"])
        self.assertFalse(interfaces.compare(python, interfaces.structured_declarations(
            "def call(value: str, extra): return value\n", "python", "api.py"))["passed"])
        baseline = interfaces.structured_declarations(
            "export function call(value: string): number {return 1}", "typescript", "api.ts")
        for changed in ["export function call(value: string, extra?: number): number {return 1}",
                        "export function call(value: string, extra: number = 1): number {return 1}"]:
            self.assertTrue(interfaces.compare(baseline, interfaces.structured_declarations(changed, "typescript", "api.ts"))["passed"])
        for changed in ["export function call(value: number): number {return 1}",
                        "export function call(value: string): string {return ''}"]:
            self.assertFalse(interfaces.compare(baseline, interfaces.structured_declarations(changed, "typescript", "api.ts"))["passed"])
