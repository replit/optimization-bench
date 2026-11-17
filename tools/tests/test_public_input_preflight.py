import hashlib
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import public_input_preflight as public


class PublicInputTests(unittest.TestCase):
    def fixture(self, root, *, body=b"public original", present=False, recipe=True):
        task = root / "tasks/example"
        task.mkdir(parents=True)
        destination = "environment/workload/inputs/files/body"
        entry = {"scope": "public", "destination": destination, "name": "body",
                 "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        if recipe:
            entry["fetch"] = {"kind": "test-fixture"}
        private = {"scope": "heldout", "destination": "tests/heldout/private",
                   "name": "private", "bytes": 6, "sha256": hashlib.sha256(b"secret").hexdigest(),
                   "fetch": {"kind": "must-not-fetch"}}
        (task / "payload.json").write_text(json.dumps({"files": [entry, private]}))
        if present:
            path = task / destination
            path.parent.mkdir(parents=True)
            path.write_bytes(body)
        return task, entry

    def test_present_public_hash_checked_no_private_body_needed(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp), present=True)
            with patch.object(public.stage_payload, "stage") as stage:
                receipt = public.ensure_public_inputs(task, fetch=False, root=Path(tmp))
            stage.assert_not_called()
            self.assertEqual(receipt["verified_entries"], 1)
            self.assertFalse(receipt["private_acquisition"])
            self.assertFalse((task / "tests/heldout/private").exists())

    def test_image_workload_body_restores_repo_lfs_public_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, entry = self.fixture(root, recipe=False)
            entry.update(kind="repo-lfs", source="public/files/body", reason="Fixture public original")
            (task / "public/files").mkdir(parents=True)
            (task / "public/files/body").write_bytes(public.POINTER + b"oid sha256:" + b"a" * 64 + b"\nsize 15\n")
            manifest = json.loads((task / "payload.json").read_text())
            manifest["files"][0] = entry
            (task / "payload.json").write_text(json.dumps(manifest))
            source = root / "cached-workload"
            (source / "inputs/files").mkdir(parents=True)
            (source / "inputs/files/body").write_bytes(b"public original")
            with patch.object(public.subprocess, "run", side_effect=AssertionError("no git/global/network recovery")):
                receipt = public.ensure_public_inputs(task, source=source, root=root)
            self.assertEqual(receipt["cache_restored_entries"], 1)
            self.assertEqual((task / entry["destination"]).read_bytes(), b"public original")
            self.assertFalse((task / "tests/heldout/private").exists())
            self.assertTrue((task / "public/files/body").read_bytes().startswith(public.POINTER))

    def test_corrupt_cached_body_rolls_back_and_readonly_never_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, entry = self.fixture(root, recipe=False)
            source = root / "cached-workload"
            source.mkdir()
            (source / "body").write_bytes(b"corrupt")
            with self.assertRaises(public.PublicInputsError):
                public.ensure_public_inputs(task, source=source, root=root)
            self.assertFalse((task / entry["destination"]).exists())
            (source / "body").write_bytes(b"public original")
            with self.assertRaises(public.PublicInputsError):
                public.ensure_public_inputs(task, source=source, fetch=False, root=root)
            self.assertFalse((task / entry["destination"]).exists())

    def test_input_free_cache_fetches_original_and_materializes_only_public_runtime_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, entry = self.fixture(root)
            data = json.loads((task / "payload.json").read_text())
            data["files"][0]["fetch"] = {
                "kind": "literal", "base64": base64.b64encode(b"public original").decode()}
            (task / "payload.json").write_text(json.dumps(data))
            source = root / "cached-workload"
            source.mkdir()
            receipt = public.ensure_public_inputs(task, source=source, root=root)
            self.assertEqual(receipt["cache_restored_entries"], 0)
            self.assertEqual(receipt["source_fetch_fallback_entries"], 1)
            self.assertEqual(receipt["runtime_cache_materialized_entries"], 1)
            self.assertEqual((source / "inputs/files/body").read_bytes(), b"public original")
            self.assertFalse((task / "tests/heldout/private").exists())
            self.assertFalse((source / "private").exists())

    def test_workload_cache_misses_do_not_block_pinned_notices_outside_workload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, _ = self.fixture(root, recipe=False)
            data = json.loads((task / "payload.json").read_text())
            notice = b"original license notice"
            data["files"].append({
                "scope": "public", "destination": "environment/notices/source-LICENSE",
                "name": "source-LICENSE", "bytes": len(notice),
                "sha256": hashlib.sha256(notice).hexdigest(), "fetch": {
                    "kind": "literal", "base64": base64.b64encode(notice).decode()}})
            (task / "payload.json").write_text(json.dumps(data))
            source = root / "cached-workload"
            (source / "inputs/files").mkdir(parents=True)
            (source / "inputs/files/body").write_bytes(b"public original")
            receipt = public.ensure_public_inputs(task, source=source, root=root)
            self.assertEqual(receipt["cache_restored_entries"], 1)
            self.assertEqual(receipt["source_fetch_fallback_entries"], 1)
            self.assertEqual((task / "environment/notices/source-LICENSE").read_bytes(), notice)
            self.assertFalse((source / "notices").exists())

    def test_existing_corrupt_cache_is_not_replaced_by_a_fetch_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, _ = self.fixture(root)
            source = root / "cached-workload"
            source.mkdir()
            (source / "body").write_bytes(b"corrupt")
            import payload_check
            with patch.object(payload_check.Fetcher, "render", side_effect=AssertionError("not a cache miss")):
                with self.assertRaises(public.PublicInputsError):
                    public.ensure_public_inputs(task, source=source, root=root)
            self.assertFalse((task / "environment/workload/inputs/files/body").exists())

    def test_missing_fetch_only_recipe_staged_then_real_body_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp))
            def render(recipe):
                self.assertEqual(recipe, {"kind": "test-fixture"})
                return b"public original"
            import payload_check
            with patch.object(payload_check.Fetcher, "render", side_effect=render):
                receipt = public.ensure_public_inputs(task, fetch=True, root=Path(tmp))
            self.assertEqual(receipt["staged_entries"], 1)
            self.assertEqual((task / entry["destination"]).read_bytes(), b"public original")
            self.assertFalse((task / "tests/heldout/private").exists())
            self.assertFalse(receipt["catalog_mutation"])

    def test_readonly_preflight_fails_before_generation_with_repair_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp))
            with patch.object(public.stage_payload, "stage") as stage:
                with self.assertRaisesRegex(public.PublicInputsError, "--fetch"):
                    public.ensure_public_inputs(task, fetch=False, root=Path(tmp))
            stage.assert_not_called()

    def test_lfs_pointer_never_accepted_and_only_public_path_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp), present=True)
            (task / entry["destination"]).write_bytes(public.POINTER + b"oid sha256:" + b"a" * 64 + b"\nsize 10\n")
            with patch.object(public.stage_payload, "stage") as stage:
                with self.assertRaises(public.PublicInputsError) as error:
                    public.ensure_public_inputs(task, root=Path(tmp))
            stage.assert_not_called()
            commands = "\n".join(error.exception.commands)
            self.assertIn("git lfs install --local", commands)
            self.assertIn("--include=tasks/example/environment/", commands)
            self.assertNotIn("heldout", commands)
            self.assertNotIn("git lfs pull\n", commands)

    def test_corrupt_acquisition_not_successful(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp))
            import payload_check
            with patch.object(payload_check.Fetcher, "render", return_value=b"wrong"):
                with self.assertRaises(public.PublicInputsError):
                    public.ensure_public_inputs(task, root=Path(tmp))
            self.assertFalse((task / "environment/workload/inputs/files/body").exists())

    def test_source_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp))
            data = json.loads((task / "payload.json").read_text())
            data["files"][0]["destination"] = "environment/../../private"
            (task / "payload.json").write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                public.ensure_public_inputs(task, root=Path(tmp))

    def test_ffmpeg_only_selected_for_public_png_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp))
            self.assertFalse(public.needs_ffmpeg(task, root=Path(tmp)))
            data = json.loads((task / "payload.json").read_text())
            data["files"][0]["fetch"] = {"kind": "png-rgb24"}
            (task / "payload.json").write_text(json.dumps(data))
            self.assertTrue(public.needs_ffmpeg(task, root=Path(tmp)))

    def test_repo_lfs_pointer_has_scoped_repair_not_generic_metadata_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp))
            data = json.loads((task / "payload.json").read_text())
            data["files"][0].update(kind="repo-lfs", source="public/files/source", reason="fixture public recovery")
            data["files"][0].pop("fetch")
            (task / "payload.json").write_text(json.dumps(data))
            source = task / "public/files/source"
            source.parent.mkdir(parents=True)
            source.write_bytes(public.POINTER + b"oid sha256:" + b"a" * 64 + b"\nsize 10\n")
            with self.assertRaises(public.PublicInputsError) as error:
                public.ensure_public_inputs(task, root=Path(tmp))
            self.assertIn("--include=tasks/example/public/files/source", "\n".join(error.exception.commands))

    def test_materialized_repo_lfs_source_can_stage_without_any_lfs_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp))
            data = json.loads((task / "payload.json").read_text())
            data["files"][0].update(kind="repo-lfs", source="public/files/source", reason="fixture public recovery")
            data["files"][0].pop("fetch")
            (task / "payload.json").write_text(json.dumps(data))
            source = task / "public/files/source"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"public original")
            value = public.ensure_public_inputs(task, root=Path(tmp))
            self.assertEqual(value["verified_entries"], 1)
            self.assertEqual((task / entry["destination"]).read_bytes(), b"public original")

    def test_stage_noop_cannot_substitute_for_real_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self.fixture(Path(tmp))
            with patch.object(public.stage_payload, "stage", return_value=0):
                with self.assertRaisesRegex(public.PublicInputsError, "still missing"):
                    public.ensure_public_inputs(task, root=Path(tmp))

    def test_real_fetch_recipe_pipeline_without_mocked_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp))
            data = json.loads((task / "payload.json").read_text())
            data["files"][0]["fetch"] = {"kind": "literal", "base64": base64.b64encode(b"public original").decode()}
            (task / "payload.json").write_text(json.dumps(data))
            result = public.ensure_public_inputs(task, root=Path(tmp))
            self.assertEqual(result["members"][0]["sha256"], hashlib.sha256(b"public original").hexdigest())
            self.assertEqual((task / entry["destination"]).read_bytes(), b"public original")
            self.assertFalse((task / "tests/heldout/private").exists())

    def test_changed_manifest_during_staging_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, entry = self.fixture(Path(tmp))
            def stage(*args, **kwargs):
                body = task / entry["destination"]
                body.parent.mkdir(parents=True)
                body.write_bytes(b"public original")
                manifest = task / "payload.json"
                manifest.write_text(manifest.read_text() + "\n")
            with patch.object(public.stage_payload, "stage", side_effect=stage):
                with self.assertRaisesRegex(public.PublicInputsError, "manifest changed"):
                    public.ensure_public_inputs(task, root=Path(tmp))


if __name__ == "__main__":
    unittest.main()
