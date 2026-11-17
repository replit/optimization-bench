"""Contract tests for tools/fetch_public_inputs.py (no network)."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import fetch_public_inputs as fpi

ROOT = Path(__file__).resolve().parents[2]
INPUT_FREE = {"jq-strip-openapi-descriptions", "serde-json-production-crate-index-ingestion", "sqlfmt-cli",
              "sqlglot-mozilla-transpile", "terser-minify"}


class FetchPublicInputsTests(unittest.TestCase):
    def test_exactly_the_input_free_images_need_fetching(self):
        found = set()
        for task in sorted((ROOT / "tasks").iterdir()):
            if (task / "payload.json").is_file() and fpi.runtime_mounted(task, fpi.public_packet.load(task)):
                found.add(task.name)
        self.assertEqual(found, INPUT_FREE)

    def test_tasks_with_inputs_in_the_image_are_refused(self):
        with self.assertRaisesRegex(fpi.FetchError, "already contains its public inputs"):
            fpi.fetch("bzip2-codec", Path(tempfile.mkdtemp()), None)

    def test_mismatched_image_manifest_fails_before_any_download(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "manifest.json"
            manifest.write_text("{}")
            with self.assertRaisesRegex(fpi.FetchError, "does not match the image"):
                fpi.fetch("terser-minify", Path(folder) / "dest", manifest)
            self.assertFalse((Path(folder) / "dest").exists())

    def test_every_runtime_input_has_a_pinned_size_and_hash(self):
        for name in sorted(INPUT_FREE):
            task = ROOT / "tasks" / name
            rows = [e for e in json.loads((task / "payload.json").read_text())["files"]
                    if e["scope"] == "public" and e["destination"].startswith(fpi.PREFIX)]
            packet = fpi.public_packet.load(task) or {}
            rows += packet.get("derived_inputs", [])
            with self.subTest(task=name):
                self.assertTrue(rows)
                for entry in rows:
                    self.assertRegex(entry["sha256"], "^[0-9a-f]{64}$")
                    self.assertIsInstance(entry["bytes"], int)

    def test_unknown_task_is_rejected(self):
        with self.assertRaises((fpi.FetchError, ValueError)):
            fpi.fetch("no-such-task", Path(tempfile.mkdtemp()), None)


if __name__ == "__main__":
    unittest.main()
