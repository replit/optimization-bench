import importlib.util
import json
from pathlib import Path
import random
import unittest

spec = importlib.util.spec_from_file_location("terser_adapter", Path(__file__).with_name("adapter.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class AdapterTests(unittest.TestCase):
    def test_deterministic_generators_cover_four_public_entries(self):
        x = list(a.generate(random.Random(1729), 128))
        self.assertEqual(x, list(a.generate(random.Random(1729), 128)))
        self.assertEqual(len(x), 128)
        self.assertEqual(len({v["surface_id"] for v in x}), 4)
        self.assertTrue(all(a.precondition(v, None)["valid"] for v in x))
        self.assertTrue(any(v["sequence"] for v in x))

    def test_private_or_mismatched_surface_rejected(self):
        value = a.case("1")
        value["surface_id"] = "internal.TreeWalker"
        self.assertFalse(a.precondition(value, None)["valid"])
        value = a.case("1")
        value["input"]["api"] = "minify_sync"
        self.assertFalse(a.precondition(value, None)["valid"])

    def test_malformed_source_and_faults_rejected(self):
        value = a.case(1)
        self.assertFalse(a.precondition(value, None)["valid"])
        value = a.case("1")
        value["faults"] = ["permanent-EINTR"]
        self.assertFalse(a.precondition(value, None)["valid"])

    def test_type_and_signed_zero_comparison(self):
        self.assertFalse(a.typed_equal(False, 0))
        self.assertFalse(a.typed_equal(1, 1.0))
        self.assertFalse(a.typed_equal(0.0, -0.0))
        self.assertTrue(a.typed_equal({"x": [1]}, {"x": [1]}))

    def test_shrinker_preserves_identity_and_fault_class(self):
        value = a.case("function f(){return 1}", sequence=[{"files": "a()"}])
        for shrunk in a.shrink(value):
            for key in ["task", "surface_id", "contract_id", "precondition_id", "faults"]:
                self.assertEqual(value[key], shrunk[key])
            self.assertTrue(a.precondition(shrunk, None)["valid"])

    def test_invalid_transport_not_equal(self):
        self.assertFalse(a.compare({}, {}, a.case("1"))["equal"])

    def test_legacy_public_conversion_not_private_surface_expansion(self):
        value = a.from_legacy_public({"kind": "minify", "source": "a()", "options": {"mangle": False}})
        self.assertTrue(a.precondition(value, None)["valid"])
        with self.assertRaises(ValueError):
            a.from_legacy_public({"kind": "walker", "source": "a()"})

    def test_public_filename_keys_are_data_not_prototype_operations(self):
        value = a.case({"__proto__": "console.log(1)", "constructor": "console.log(2)"})
        self.assertTrue(a.precondition(value, None)["valid"])
        self.assertIn("__proto__", json.dumps(value))
        different = a.case({"constructor": "console.log(2)"})
        self.assertNotEqual(a.digest(value), a.digest(different))

    def test_controller_digest_preserves_float_spelling_and_rejects_nan(self):
        value = a.case("1", {"compress": {"inline": 1.0}})
        request = a.request(value)
        self.assertEqual(request["case_sha256"], a.digest(value))
        self.assertEqual(request["case"], value)
        value["options"]["compress"]["inline"] = float("nan")
        self.assertFalse(a.precondition(value, None)["valid"])
        with self.assertRaises(ValueError):
            a.request(value)

if __name__ == "__main__":
    unittest.main()
