import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("recover_serde_goldens",
    ROOT / "tools/recover_serde_json_goldens.py")
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class LegacyGoldenRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task, self.bundle, self.inputs, self.output = [
            self.root / name for name in ("task", "scorer-metadata", "inputs", "restored")]
        (self.task / "tests").mkdir(parents=True)
        self.inputs.mkdir()
        self.module = recovery.load_task(recovery.TASK)
        self.producer_called = False
        docs, references, payload, locked = [], {}, [], {}
        self.values = {}
        for name in recovery.NAMES:
            raw = json.dumps({"original": name}).encode()
            digest = hashlib.sha256(raw).hexdigest()
            (self.inputs / digest).write_bytes(raw)
            value = {"versions": [{"name": name, "features": {"unicode": ["λ"]}}]}
            self.values[name] = value
            expected = self.module.canonical_json(value)
            expected_sha = hashlib.sha256(expected).hexdigest()
            url = "https://example.invalid/frozen/" + name
            docs.append({"path": name, "bytes": len(raw), "sha256": digest, "url": url,
                         "expect_len": len(expected), "expect_sha": expected_sha})
            references["heldout/files/" + digest] = {
                "kind": "file", "role": "input", "scope": "heldout", "url": url,
                "bytes": len(raw), "sha256": digest,
                "license_basis": "fetch-only; no redistribution"}
            payload.append({**references["heldout/files/" + digest],
                            "name": "heldout/files/" + digest, "retention": "fetch-only",
                            "destination": "tests/heldout/files/" + digest})
            destination = "tests/heldout/expected/" + name
            payload.append({"destination": destination, "name": destination, "scope": "heldout",
                            "bytes": len(expected), "sha256": expected_sha})
            locked[destination.removeprefix("tests/")] = expected_sha
        manifest = {"splits": {"heldout": [{"id": "distinct-registry-corpus", "docs": docs}]}}
        for name in recovery.METADATA:
            raw = json.dumps(manifest if name.endswith("/manifest.json") else
                             {"original_receipt": name}).encode()
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            payload.append({"destination": name, "name": name, "scope": "heldout",
                            "bytes": len(raw), "sha256": sha})
            locked[name.removeprefix("tests/")] = sha
        (self.task / "payload.json").write_text(json.dumps({"files": payload}))
        (self.task / "tests/heldout.lock.json").write_text(json.dumps(
            {"schema_version": 1, "status": "available", "files": locked, "references": references}))
        (self.task / "tests/judge.json").write_text(json.dumps({"memory": "16g", "pids_limit": 512}))

    def run_recovery(self, mutate=None, **options):
        def produce(steps, ctx, environment):
            self.producer_called = True
            self.assertEqual(steps[0].argv, [
                "/app/baseline-build/prog", "--check-batch",
                "/scratch/batch-list.tsv", "/scratch/outputs"])
            self.assertFalse(steps[0].resident)
            self.assertEqual(len(list(ctx.inputs.iterdir())), 6)
            outputs = ctx.scratch / "outputs"
            outputs.mkdir()
            for name, value in self.values.items():
                (outputs / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2))
            if mutate:
                mutate(outputs)
            return [SimpleNamespace(exit_code=0, stdout=b"", stderr=b"", leftovers=[])]
        with patch.object(recovery.telemetry, "pinned") as pinned, \
                patch.object(recovery, "image_id", return_value="sha256:original-image"), \
                patch.object(recovery.image_recipe, "observation", return_value={
                    "recipe_label_matches": True, "recipe_sha256": "original-recipe"}), \
                patch.object(recovery, "load_task", return_value=self.module), \
                patch.object(recovery, "DockerExecutor") as executor:
            executor.return_value.run.side_effect = produce
            arguments = {"inputs": self.inputs, **options}
            result = recovery.recover(self.task, self.bundle, "original-image", "0-7",
                                      self.output, **arguments)
            pinned.assert_called_once_with("0-7")
            flags = executor.call_args.kwargs["extra_docker_flags"]
            self.assertTrue(any(flag.endswith(":/recovery-inputs:ro") for flag in flags))
            self.assertEqual(executor.call_args.kwargs["memory"], "16g")
            return result

    def test_restores_all_thirteen_originals_without_writing_task_paths(self):
        result = self.run_recovery()
        self.assertEqual(result["restored_files"], 13)
        self.assertEqual(result["recovered_goldens"], 6)
        self.assertFalse(result["timing_performed"])
        self.assertFalse(result["claimable"])
        self.assertEqual(len([p for p in self.output.rglob("*") if p.is_file()]), 13)
        self.assertFalse((self.task / "tests/heldout").exists())
        for name in recovery.NAMES:
            self.assertEqual((self.output / "tests/heldout/expected" / name).read_bytes(),
                             self.module.canonical_json(self.values[name]))
        for name in recovery.METADATA:
            self.assertEqual((self.output / name).read_bytes(), (self.bundle / name).read_bytes())
        with patch.object(recovery.stage_payload.sys, "path", [
                str(ROOT / "tools"), *recovery.stage_payload.sys.path]):
            self.assertEqual(recovery.stage_payload.stage(
                self.task, source=self.output, scope="heldout"), 13)
        retained, _, _ = recovery.originals(self.task, self.bundle)
        for name, entry in retained.items():
            self.assertTrue(recovery.stage_payload.authentic(self.task / name, entry))

    def test_mismatched_output_fails_closed_without_changing_frozen_pins(self):
        before = (self.task / "tests/heldout.lock.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Frozen golden length/SHA-256 mismatch"):
            self.run_recovery(lambda outputs: (outputs / "winit.json").write_text('{"wrong":true}'))
        self.assertFalse(self.output.exists())
        self.assertEqual((self.task / "tests/heldout.lock.json").read_bytes(), before)
        self.assertFalse((self.task / "tests/heldout").exists())

    def test_duplicate_output_key_uses_existing_strict_json_rejection(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            self.run_recovery(lambda outputs: (outputs / "bevy.json").write_text('{"x":1,"x":2}'))
        self.assertFalse(self.output.exists())

    def test_corrupt_input_rejected_before_any_producer_execution(self):
        next(self.inputs.iterdir()).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Original length/SHA-256 mismatch"):
            self.run_recovery()
        self.assertFalse(self.producer_called)
        self.assertFalse(self.output.exists())

    def test_explicit_fetch_requests_only_the_original_six_input_records(self):
        with patch.object(recovery.heldout, "git_tools") as guard:
            fetcher = guard.return_value.Fetcher.return_value
            fetcher.url.side_effect = lambda row: self.inputs / row["sha256"]
            self.run_recovery(inputs=None, fetch=True, cache=self.root / "external-cache")
            self.assertEqual(fetcher.url.call_count, 6)
            self.assertEqual({call.args[0]["url"] for call in fetcher.url.call_args_list},
                             {"https://example.invalid/frozen/" + name for name in recovery.NAMES})

    def test_missing_input_does_not_implicitly_fetch(self):
        next(self.inputs.iterdir()).unlink()
        with patch.object(recovery.heldout, "git_tools") as fetcher:
            with self.assertRaisesRegex(ValueError, "supply --inputs or explicit --fetch"):
                self.run_recovery()
            fetcher.assert_not_called()

    def test_corrupt_metadata_rejected_before_image_or_producer_access(self):
        (self.bundle / recovery.METADATA[-1]).write_bytes(b"corrupt")
        with patch.object(recovery, "image_id") as image:
            with self.assertRaisesRegex(ValueError, "Restore the original ordinary-Git scorer metadata"):
                recovery.recover(self.task, self.bundle, "image", "0-7", self.output)
            image.assert_not_called()

    def test_nested_lfs_pointer_cannot_substitute_for_raw_bundle_metadata(self):
        (self.bundle / recovery.METADATA[0]).write_bytes(
            b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 1\n")
        with patch.object(recovery, "image_id") as image:
            with self.assertRaisesRegex(ValueError, "not a Git LFS pointer"):
                recovery.recover(self.task, self.bundle, "image", "0-7", self.output)
            image.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_wrong_recipe_never_executes_a_producer(self):
        with patch.object(recovery.telemetry, "pinned"), \
                patch.object(recovery, "image_id", return_value="sha256:other-image"), \
                patch.object(recovery.image_recipe, "observation", return_value={
                    "recipe_label_matches": False}), \
                patch.object(recovery, "DockerExecutor") as executor:
            with self.assertRaisesRegex(ValueError, "canonical pristine SDK"):
                recovery.recover(self.task, self.bundle, "image", "0-7", self.output)
            executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
