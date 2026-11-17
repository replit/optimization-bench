import gzip
import json
from pathlib import Path
import tempfile
import unittest
from judge.upstream import validate_receipt


class CompressedReceipts(unittest.TestCase):
    def test_compressed_bound_ledger_retains_the_same_validation(self):
        suite = {"upstream": {"passed": True, "outcomes": {"original-test-identity": "passed"}}}
        value = {"task": "example", "image": "image", "source_sha256": "source",
                 "build_sha256": "build", "baseline": suite, "candidate": suite}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "upstream.json.gz"
            path.write_bytes(gzip.compress(json.dumps(value).encode(), mtime=0))
            self.assertEqual(validate_receipt(path, "example", "image", "source", "build"), value)
            with self.assertRaisesRegex(ValueError, "not bound"):
                validate_receipt(path, "example", "different-image", "source", "build")
