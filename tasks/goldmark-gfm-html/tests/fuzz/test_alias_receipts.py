"""Keep recorded public-instance aliasing control distinguishable."""
import gzip
import json
from pathlib import Path
import unittest


class AliasReceiptTests(unittest.TestCase):
    def test_actual_shared_document_control_changes_contractual_effects(self):
        root=Path(__file__).parent/"receipts/rc-alias-and-managed-go"
        facts=json.loads(gzip.decompress((root/"alias-negative-facts.json.gz").read_bytes()))
        row=facts["cases"][0]
        self.assertEqual(len(row["baseline"]),2)
        self.assertEqual(len(row["candidate"]),2)
        for left,right in zip(row["baseline"],row["candidate"]):
            self.assertTrue(left["effects"]["aliasing_steps"][0]["documents_distinct"])
            self.assertFalse(right["effects"]["aliasing_steps"][0]["documents_distinct"])
            self.assertNotEqual(left["effects"],right["effects"])
        negative=json.loads(gzip.decompress((root/"alias-negative-classified.json.gz").read_bytes()))
        positive=json.loads(gzip.decompress((root/"aliasing-classified.json.gz").read_bytes()))
        self.assertEqual(negative["status"],"contract_regression")
        self.assertEqual(positive["status"],"passed")
