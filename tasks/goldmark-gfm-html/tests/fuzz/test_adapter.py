import importlib.util
import random
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("goldmark_adapter",Path(__file__).with_name("adapter.py"))
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
class AdapterTests(unittest.TestCase):
    def case(self):
        return next(adapter.generate(random.Random(1729),{"max_cases":1}))
    def observation(self,case):
        return {"format":"optimbench-fuzz-observation-v1",
                "identity":{"task":case["task"],"surface_id":case["surface_id"],
                            "case_sha256":case["case_sha256"],"build_sha256":"a"*64},
                "termination":{"kind":"return","exit_code":0},
                "outcome":{"kind":"ok","value":None},
                "effects":{"html_utf8":"<p>x</p>\n"},"diagnostics":{}}
    def test_deterministic_bounded_generation_and_shrink(self):
        a=list(adapter.generate(random.Random(1729),{"max_cases":12}))
        self.assertEqual(a,list(adapter.generate(random.Random(1729),{"max_cases":12})))
        for c in a:
            self.assertTrue(adapter.precondition(c,{})["valid"])
            for small in adapter.shrink(c):
                self.assertTrue(adapter.precondition(small,{})["valid"])
                self.assertEqual(small["case_sha256"],adapter.digest(small))
    def test_fault_is_not_silently_ignored(self):
        c=self.case();c["faults"]=[{"kind":"unknown"}]
        c["case_sha256"]=adapter.digest(c)
        self.assertFalse(adapter.precondition(c,{})["valid"])
    def test_complete_effects_and_errors_are_compared(self):
        c=self.case();a=self.observation(c);b=self.observation(c)
        self.assertTrue(adapter.compare(a,b,c)["equal"])
        b["effects"]["html_utf8"]="wrong"
        self.assertFalse(adapter.compare(a,b,c)["equal"])
        b=self.observation(c);b["outcome"]={"kind":"error","class":"io","message":"different"}
        self.assertFalse(adapter.compare(a,b,c)["equal"])
    def test_malformed_and_wrong_identity_never_equal(self):
        c=self.case();a=self.observation(c)
        self.assertFalse(adapter.compare({}, {},c)["equal"])
        b=self.observation(c);b["identity"]["case_sha256"]="wrong"
        self.assertFalse(adapter.compare(a,b,c)["equal"])
    def test_unconstrained_diagnostics_are_not_contractual(self):
        c=self.case();a=self.observation(c);b=self.observation(c)
        b["diagnostics"]={"write_calls":17}
        self.assertTrue(adapter.compare(a,b,c)["equal"])
    def test_bool_number_and_signed_zero_are_not_coerced(self):
        for left,right in ((False,0),(True,1),(1,1.0),(0.0,-0.0)):
            self.assertFalse(adapter.typed_equal({"value":[left]},{"value":[right]}))
        c=self.case();a=self.observation(c);b=self.observation(c)
        a["effects"]={"accepted_bytes":False};b["effects"]={"accepted_bytes":0}
        self.assertFalse(adapter.compare(a,b,c)["equal"])
    def test_root_selfhash_is_validated_and_nested_hash_is_preserved(self):
        c=self.case();c["case_sha256"]="0"*64
        self.assertFalse(adapter.precondition(c,{})["valid"])
        c=self.case();before=adapter.digest(c)
        c["input"]["case_sha256"]="nested-value"
        self.assertNotEqual(before,adapter.digest(c))
    def test_ast_alias_domain_uses_only_legal_construction_and_mutation(self):
        c={"task":adapter.TASK,"surface_id":adapter.ALIAS_SURFACE,
           "contract_id":"goldmark.public.ast-instance-isolation",
           "precondition_id":"goldmark.public.ast-construction-and-mutation",
           "input":{"metadata_value":"independent"},"options":{"mode":"ast_metadata"},
           "sequence":["factory","getter","mutate","interleave","fresh","finish"],"faults":[]}
        c["case_sha256"]=adapter.digest(c)
        self.assertTrue(adapter.precondition(c,{})["valid"])
        for small in adapter.shrink(c):
            self.assertTrue(adapter.precondition(small,{})["valid"])
        c["faults"]=[{"kind":"fail_after","limit":0}]
        c["case_sha256"]=adapter.digest(c)
        self.assertFalse(adapter.precondition(c,{})["valid"])
if __name__=="__main__":unittest.main()
