"""Shared transport/release boundaries, not native task-judge qualification."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from judge import score
from tools import release_preflight


class ReleaseTests(unittest.TestCase):
    def test_selected_definitions_have_consistent_transport_contracts(self):
        result = release_preflight.inspect_release()
        selected = json.loads((release_preflight.ROOT / "datasets/v1.0.json").read_text())["tasks"]
        self.assertEqual(len(result["tasks"]), len(selected))
        self.assertEqual({row["task"] for row in result["tasks"]}, set(selected))
        self.assertTrue(result["passed"], result)
        self.assertTrue(all(t["judge_smoke_test"] == "not_run" for t in result["tasks"]))
        for row in result["tasks"]:
            rules = json.loads((release_preflight.ROOT / "tasks" / row["task"] /
                                "release.json").read_text())["source_policy"]
            files = (rules.get("operations") == ["modify"] and
                     rules["source_roots"] == ["."] and bool(rules.get("allowed_files")))
            self.assertEqual(row["artifact_kind"], "files" if files else "directories")
            self.assertEqual(row["exports"], rules["allowed_files"] if files else rules["source_roots"])

    def test_old_release_does_not_admit_current_task_versions(self):
        with self.assertRaisesRegex(ValueError, "Unknown release"):
            release_preflight.inspect_release(release="v1.1", tasks=["numpy-quantile"])

    def test_membership_and_release_are_validated(self):
        for kwargs in ({"release": "../wrong"}, {"tasks": ["missing-task"]}):
            with self.assertRaises(ValueError):
                release_preflight.inspect_release(**kwargs)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.task = self.root / "task"
        self.task.mkdir()
        self.exports = self.root / "exports"
        self.exports.mkdir()
        self.rules = {"source_root": "/app/example", "source_roots": ["."],
                      "operations": ["modify"], "allowed_files": ["a.c", "b.c"]}
        self.configure()
        self.map = {n: n for n in ("a.c", "b.c")}
        for name in self.map:
            (self.exports / name).write_text("source")
        self.descriptor = self.exports / "artifacts.json"

    def configure(self):
        (self.task / "release.json").write_text(json.dumps({"source_policy": self.rules}))
        (self.task / "task.toml").write_text(
            'artifacts = [{source="/app/example/a.c"}, {source="/app/example/b.c"}]\n')

    def load(self):
        self.descriptor.write_text(json.dumps(self.map))
        return score.artifacts(self.task, self.descriptor)[0]

    def test_modify_only_file_map_reaches_the_existing_admission_contract(self):
        mapping = self.load()
        self.assertEqual(set(mapping), {"a.c", "b.c"})
        self.assertTrue(all(Path(p).is_file() for p in mapping.values()))

    def test_harbor_collected_files_are_mapped_without_a_directory_export(self):
        trial = self.root / "trial"
        source = trial / "artifacts/app/example"
        source.mkdir(parents=True)
        for name in self.map:
            (source / name).write_text("source")
        with patch.object(score.gates, "source_path", return_value="/app/example"):
            mapping, _ = score.artifacts(self.task, trial)
        self.assertEqual(set(mapping), set(self.map))

    def test_whole_root_export_cannot_broaden_modify_only_file_policy(self):
        self.map = {".": "."}
        with self.assertRaisesRegex(ValueError, "declared exports"):
            self.load()

    def test_malformed_descriptor_is_a_controlled_admission_error(self):
        self.descriptor.write_text("[]")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            score.artifacts(self.task, self.descriptor)

    def test_missing_and_extra_exports_are_rejected(self):
        for mapping in ({"a.c": "a.c"}, {**self.map, "api.h": "a.c"}):
            self.map = mapping
            with self.assertRaisesRegex(ValueError, "declared exports"):
                self.load()

    def test_wrong_kind_and_symlink_are_rejected_before_build(self):
        (self.exports / "a.c").unlink()
        (self.exports / "a.c").mkdir()
        with self.assertRaisesRegex(ValueError, "must be a file"):
            self.load()
        (self.exports / "a.c").rmdir()
        (self.exports / "a.c").symlink_to("b.c")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.load()

    def test_directory_export_remains_supported(self):
        self.rules = {"source_roots": ["src"], "operations": ["modify", "add", "remove"]}
        self.configure()
        (self.exports / "src").mkdir()
        self.map = {"src": "src"}
        self.assertEqual(set(self.load()), {"src"})

    def test_failed_harbor_collection_is_not_admitted(self):
        (self.exports / "manifest.json").write_text(json.dumps({
            "entries": [{"source": "/app/example/a.c", "status": "failed"}]}))
        with patch.object(score.gates, "source_path", return_value="/app/example"):
            with self.assertRaisesRegex(ValueError, "did not successfully collect"):
                score.artifacts(self.task, self.exports)


class CollectionVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / "tasks/legacy-example"
        (self.task / "tests").mkdir(parents=True)
        (self.root / "datasets").mkdir()
        self.selection = {"release": "optimbench-v1.0", "task_version": "1.0.0",
                          "tasks": ["legacy-example"],
                          "task_versions": {"legacy-example": "0.2.0"}}
        self.release = {"source_policy": {"source_roots": ["src"]}}
        self.definition = {}
        self.settings = json.loads((release_preflight.ROOT /
            "tasks/numpy-quantile/tests/judge.json").read_text())
        (self.task / "task.toml").write_text(
            '[task]\nversion = "0.2.0"\n[[artifacts]]\nsource = "/app/example/src"\n')
        (self.task / "tests/heldout.lock.json").write_text("{}")
        (self.task / "payload.json").write_text("{}")
        self.write()

    def write(self):
        for path, value in ((self.root / "datasets/v1.0.json", self.selection),
                            (self.task / "release.json", self.release),
                            (self.task / "task.json", self.definition),
                            (self.task / "tests/judge.json", self.settings)):
            path.write_text(json.dumps(value))

    def test_collection_explicitly_preserves_legacy_package_version(self):
        result = release_preflight.inspect_release(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["tasks"][0]["task_version"], "0.2.0")
        self.assertEqual(result["tasks"][0]["judge_smoke_test"], "not_run")

    def test_legacy_version_is_not_inferred_from_an_inconsistent_selection(self):
        del self.selection["task_versions"]
        self.write()
        self.assertFalse(release_preflight.inspect_release(self.root)["passed"])

    def test_every_present_version_field_must_agree(self):
        for document in (self.release, self.definition):
            with self.subTest(document=document):
                document["task_version"] = "1.0.0"
                self.write()
                self.assertFalse(release_preflight.inspect_release(self.root)["passed"])
                del document["task_version"]

    def test_unknown_or_malformed_version_overrides_are_rejected(self):
        for value in ([], {"absent-task": "0.2.0"}):
            self.selection["task_versions"] = value
            self.write()
            with self.assertRaisesRegex(ValueError, "version overrides"):
                release_preflight.inspect_release(self.root)

    def test_legacy_membership_does_not_force_neutral_prompt_conversion(self):
        from tools import update_catalog
        self.selection["tasks"].append("modern-example")
        self.write()
        with patch.object(update_catalog, "PROMPT_REPOSITORY", self.root):
            self.assertEqual(update_catalog.neutral_prompt_tasks(), {"modern-example"})


class PublicSetupTests(unittest.TestCase):
    def test_direct_https_and_transport_urls_are_fetchable_not_operator_packets(self):
        from tools import update_catalog
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "example"
            (task / "environment").mkdir(parents=True)
            entries = [
                {"name": "direct", "scope": "public", "destination": "environment/inputs/direct",
                 "bytes": 2, "sha256": "a" * 64, "url": "https://example.invalid/direct.csv"},
                {"name": "transport", "scope": "public", "destination": "environment/inputs/transport",
                 "bytes": 3, "sha256": "b" * 64, "transport": {
                     "kind": "png-rgb24", "url": "https://example.invalid/input.png"}},
                {"name": "operator", "scope": "public", "destination": "environment/inputs/operator",
                 "bytes": 7, "sha256": "c" * 64},
                {"name": "private", "scope": "heldout", "destination": "tests/heldout/private",
                 "bytes": 11, "sha256": "d" * 64, "url": "https://example.invalid/heldout"}]
            (task / "payload.json").write_text(json.dumps({"files": entries}))
            with patch.object(update_catalog, "task_paths", return_value=[task]), patch.object(
                    update_catalog, "unavailable_inputs", return_value=[]), patch.object(
                    update_catalog.subprocess, "run",
                    return_value=SimpleNamespace(returncode=0, stdout=b"")):
                update_catalog.normalize_distribution([task])
            result = update_catalog.distribution(task)
            self.assertEqual(result["upstream"], 5)
            self.assertEqual(result["operator"], 7)
            self.assertEqual(result["upstream_urls"], [
                "https://example.invalid/direct.csv", "https://example.invalid/input.png"])

    def test_mixed_lfs_and_upstream_setup_fetches_all_declared_inputs(self):
        from tools import update_catalog
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "mixed-inputs"
            task.mkdir()
            (task / "task.json").write_text("{}")
            data = {"git": 1, "upstream": 2, "repo-lfs": 3, "operator": 0,
                    "upstream_urls": ["https://example.org/source"]}
            with patch.object(update_catalog, "distribution", return_value=data):
                text, _ = update_catalog.task_readmes(task, "public-payload-missing")
            self.assertIn("stage_payload.py --task mixed-inputs --fetch", text)
            self.assertIn("--include='tasks/mixed-inputs/public/files/**' --exclude=''", text)

    def test_verifier_notices_and_protected_interface_helper_are_permitted(self):
        from tools.check_tasks import OPTIONAL_NAMES
        self.assertIn("notices", OPTIONAL_NAMES["tests"])
        self.assertIn("interface_snapshot.py", OPTIONAL_NAMES["environment/workload"])
        self.assertNotIn("heldout", OPTIONAL_NAMES["environment"])

    def test_fetch_only_commitments_do_not_promise_distributed_expected_bodies(self):
        from tools import update_catalog
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "external-inputs"
            (task / "tests").mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "tests/heldout.lock.json").write_text(json.dumps({
                "status": "available", "references": {"input": {}}, "files": {}}))
            data = {"git": 1, "upstream": 2, "repo-lfs": 0, "operator": 0,
                    "upstream_urls": ["https://example.org/source"]}
            with patch.object(update_catalog, "distribution", return_value=data):
                text, _ = update_catalog.task_readmes(task, "heldout-missing")
            self.assertIn("Expected-result commitments remain authenticated", text)
            self.assertNotIn("Expected outputs are retained via Git LFS", text)


if __name__ == "__main__":
    unittest.main()
