"""Release unit grouping and retained input attribution, without native claims."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TASK = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("black_release_inventory", Path(__file__).with_name("inventory.py"))
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


class PublicReleaseTests(unittest.TestCase):
    def test_four_timed_units_exclude_the_startup_correctness_probe(self):
        settings = json.loads((TASK / "tests/judge.json").read_text())
        self.assertEqual(set(settings["performance_units"]["public"]),
                         {"django-reformat", "sqlalchemy-check", "warm-snippets", "editor-ranges"})
        self.assertEqual(len(settings["performance_units"]["heldout"]), 4)
        self.assertEqual(settings["case_roles"]["public"]["startup-empty"], "correctness")
        self.assertEqual(len(settings["case_roles"]["public"]), 5)
        for cases in settings["performance_units"]["public"].values():
            self.assertNotIn("startup-empty", cases)
        self.assertEqual(settings["startup_probe"]["maximum_share"], 0.02)

    def test_pinned_heldout_api_licenses_are_present_and_authenticated(self):
        notice_root = TASK / "tests/fuzz/notices/heldout"
        notices = json.loads((notice_root / "sources.json").read_text())
        sources = json.loads((TASK / "tests/heldout/sources.json").read_text())
        api = sources["api_workloads_2026_10_29"]
        expected = {row["repository"]: row["commit"] for row in api["warm-snippets"]["sources"]}
        expected["numpy/numpy"] = api["editor-ranges"]["window_head"]
        self.assertEqual({row["repository"]: row["commit"] for row in notices}, expected)
        for row in notices:
            self.assertTrue(row["license_files"], row["repository"])
            for item in row["license_files"]:
                data = (notice_root / row["repository"].replace("/", "_") / item["path"]).read_bytes()
                self.assertEqual(len(data), item["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertEqual(item["url"], "https://raw.githubusercontent.com/"
                                 + row["repository"] + "/" + row["commit"] + "/" + item["path"])

    def test_heldout_attribution_stays_outside_the_agent_context(self):
        self.assertFalse((TASK / "notices").exists())
        self.assertFalse((TASK / "environment/notices/heldout").exists())

    def test_retained_public_files_restore_into_an_empty_runtime_cache(self):
        from judge import public_input_preflight
        entries = [entry for entry in json.loads((TASK / "payload.json").read_text())["files"]
                   if entry["scope"] == "public"]
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            receipt = public_input_preflight.ensure_public_inputs(
                TASK, source=cache, root=TASK.parents[1])
            self.assertEqual(receipt["verified_entries"], len(entries))
            self.assertEqual(receipt["runtime_cache_materialized_entries"], len(entries))
            self.assertFalse(receipt["private_acquisition"])
            for entry in entries:
                self.assertTrue(entry["destination"].startswith("environment/workload/"))
                data = (cache / entry["destination"].removeprefix("environment/workload/")).read_bytes()
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            self.assertFalse((cache / "tests").exists())

    def test_header_review_is_bound_to_the_complete_source_inventory(self):
        proof = json.loads((TASK / "tests/fuzz/header-scope.json").read_text())
        actual = INVENTORY.header_review(proof["files_scanned"], proof["file_inventory_sha256"])
        self.assertEqual(actual, proof["independent_review"])
        for count, digest in ((459, proof["file_inventory_sha256"]), (460, "0" * 64)):
            with self.assertRaisesRegex(ValueError, "Refresh"):
                INVENTORY.header_review(count, digest)

    def test_header_review_rejects_private_provenance_or_a_different_archive(self):
        proof = json.loads((TASK / "tests/fuzz/header-scope.json").read_text())
        for key, value in (("chat_reference", "private-review"),
                           ("independently_verified_archive_sha256", "0" * 64)):
            with tempfile.TemporaryDirectory() as temporary:
                altered = json.loads(json.dumps(proof))
                altered["independent_review"][key] = value
                (Path(temporary) / "header-scope.json").write_text(json.dumps(altered))
                with patch.object(INVENTORY, "OUT", Path(temporary)):
                    with self.assertRaisesRegex(ValueError, "Refresh"):
                        INVENTORY.header_review(proof["files_scanned"], proof["file_inventory_sha256"])


if __name__ == "__main__":
    unittest.main()
