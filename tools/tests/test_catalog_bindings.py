"""Refreshing package paths must never rewrite upstream data commitments."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import update_catalog
import stage_payload
import check_tasks
from tools.tests.task_fixture import without_private_payload


class ContentBindingTests(unittest.TestCase):
    def test_authored_input_edition_note_changes_only_task_readme_sentence(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "example"
            (task / "environment").mkdir(parents=True)
            definition = task / "task.json"
            definition.write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            (task / "environment/Dockerfile").write_text("COPY workload/ /app/workload/\n")
            before, environment_before = update_catalog.task_readmes(task, "ready")
            self.assertNotIn("No input bytes or expected results were changed.", before)
            note = ("Original public inputs and expectations are unchanged; "
                    "this new held-out edition has newly authored expectations.")
            self.assertNotIn(note, before)
            definition.write_text(json.dumps({"input_edition_note": note}))
            after, environment_after = update_catalog.task_readmes(task, "ready")
            self.assertEqual(after.count(note), 1)
            self.assertEqual(after.replace(note, "", 1).split(), before.split())
            self.assertEqual(environment_after, environment_before)

    def test_invalid_authored_input_edition_notes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            for note in (None, "", " \n\t", False, 0, [], {}):
                with self.subTest(note=note):
                    (task / "task.json").write_text(json.dumps({"input_edition_note": note}))
                    with self.assertRaisesRegex(ValueError, "input_edition_note"):
                        update_catalog.task_readmes(task, "ready")

    def test_declared_derived_packet_uses_shared_scoped_helper_not_files_only_or_bare_harbor(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "derived-example"
            (task / "environment").mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            (task / "environment/Dockerfile").write_text(
                "RUN --mount=type=bind,target=/app/workload/inputs/data check-original\n")
            (task / "environment/public-input-packet.json").write_text(
                '{"schema":"optimbench-public-input-packet-v1","mounts":["inputs/data"]}')
            text, environment = update_catalog.task_readmes(task, "ready")
            self.assertIn("python3 -m tools.public_runtime_packet --task derived-example", text)
            self.assertIn("Acquisition archives are not implicitly", text)
            self.assertNotIn("source=$packet,target=/app/workload/inputs/files", text)
            self.assertNotIn("harbor run --path", text)
            self.assertIn("The generated agent command is agent-only", environment)

    def test_external_input_recipe_documents_real_packet_setup_without_bare_harbor(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "packet-example"
            (task / "environment").mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            (task / "environment/Dockerfile").write_text(
                "RUN --mount=type=bind,source=workload/inputs/files,"
                "target=/app/workload/inputs/files,readonly check-original-inputs\n")
            text, environment = update_catalog.task_readmes(task, "ready")
            self.assertIn("stage_payload.py --task packet-example --fetch", text)
            self.assertIn("docker build --provenance=false", text)
            self.assertIn('org.optimbench.recipe-sha256=$recipe', text)
            self.assertIn("source=$packet,target=/app/workload/inputs/files,readonly", text)
            self.assertNotIn("harbor run --path", text)
            self.assertIn("Audit all image layers and metadata", environment)
            self.assertNotIn("acquires larger public inputs", environment)

    def test_image_contained_input_recipe_keeps_original_run_examples(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "image-example"
            (task / "environment").mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            (task / "environment/Dockerfile").write_text("COPY workload/ /app/workload/\n")
            text, environment = update_catalog.task_readmes(task, "ready")
            self.assertIn("docker run --rm --network none optimbench:image-example", text)
            self.assertNotIn("harbor run --path", text)
            self.assertIn("docs/agent-runner.md", text)
            self.assertIn("acquires larger public inputs", environment)
            self.assertNotIn("source=$packet", text)

    def test_fuzz_adapters_are_admitted_bound_and_stay_verifier_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "sqlglot-mozilla-transpile"
            shutil.copytree(TOOLS.parent / "tasks/sqlglot-mozilla-transpile", task)
            fuzz = task / "tests/fuzz"
            fuzz.mkdir(exist_ok=True)
            adapter = fuzz / "adapter.py"
            adapter.write_text("# Private verifier adapter, never in the agent context.\n")
            without_private_payload(task)  # Refresh the fixture's authenticated bindings.
            self.assertEqual(check_tasks.check_task(task)["task"], task.name)
            self.assertFalse((task / "environment/fuzz").exists())
            bindings = json.loads((task / "provenance.json").read_text())["current_files_sha256"]
            self.assertEqual(bindings["tests/fuzz/adapter.py"], hashlib.sha256(adapter.read_bytes()).hexdigest())
            (fuzz / "escape").symlink_to(task / "environment")
            with self.assertRaisesRegex(AssertionError, "verifier ownership"):
                check_tasks.check_task(task)
            (fuzz / "escape").unlink()
            (task / "environment/fuzz").mkdir()
            with self.assertRaisesRegex(AssertionError, "undocumented shell entry"):
                check_tasks.check_task(task)

    def test_optional_research_document_is_not_in_agent_context_or_generated_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "sqlglot-mozilla-transpile"
            shutil.copytree(TOOLS.parent / "tasks/sqlglot-mozilla-transpile", task)
            without_private_payload(task)
            status = json.loads((task / "qualification-status.json").read_text())
            catalog = ([task], [], {task: status},
                       update_catalog.START + "\n" + update_catalog.END)
            with patch.object(update_catalog, "catalog", return_value=catalog):
                before = update_catalog.generated()
                environment_before = {p.relative_to(task / "environment"): p.read_bytes()
                                      for p in (task / "environment").rglob("*") if p.is_file()}
                (task / "RESEARCH.md").write_text("# Workload research\nOptional evidence only.\n")
                self.assertEqual(check_tasks.check_task(task)["task"], task.name)
                after = update_catalog.generated()
            self.assertFalse((task / "environment/RESEARCH.md").exists())
            self.assertEqual(environment_before, {
                p.relative_to(task / "environment"): p.read_bytes()
                for p in (task / "environment").rglob("*") if p.is_file()})
            for name in ("instruction.md", "provenance.json"):
                self.assertEqual(before[task / name], after[task / name])
            self.assertNotIn(task / "RESEARCH.md", after)

    def test_readme_verification_uses_actual_heldout_lock_not_public_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary) / "example"
            (task / "tests").mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            for lock_status in ("available", "unavailable"):
                (task / "tests/heldout.lock.json").write_text(json.dumps({"status": lock_status}))
                for payload_status in ("ready", "public-payload-missing"):
                    with self.subTest(lock=lock_status, public=payload_status):
                        text, _ = update_catalog.task_readmes(task, payload_status)
                        if lock_status == "available":
                            self.assertIn("available via Git LFS and are authenticated at scoring", text)
                            self.assertNotIn("Held-out assets are unavailable", text)
                        else:
                            self.assertIn("Held-out assets are unavailable; scored verification fails closed", text)

    def test_upstream_bare_paths_stay_frozen(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "tasks/example"
            environment = task / "environment"
            environment.mkdir(parents=True)
            (task / "task.json").write_text("{}")
            (task / "payload.json").write_text('{"files":[]}')
            (task / "README.md").write_text("task README, not the project README")
            (environment / "helper.py").write_text("protected package helper")
            old = "a" * 64
            source = {"cases": [{"files_sha256": {"README.md": old}}],
                      "copied_files_sha256": {"environment/helper.py": old}}
            path = environment / "public-artifacts.json"
            path.write_text(json.dumps(source))
            with patch.object(update_catalog, "ROOT", root):
                update_catalog.refresh_bindings()
            after = json.loads(path.read_text())
            self.assertEqual(after["cases"], source["cases"])
            self.assertEqual(after["copied_files_sha256"]["environment/helper.py"],
                             hashlib.sha256((environment / "helper.py").read_bytes()).hexdigest())

    def test_staging_settles_status_and_dependent_content_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "tasks/example"
            environment = task / "environment"
            environment.mkdir(parents=True)
            source = root / "source"
            source.mkdir()
            (source / "blob").write_bytes(b"public")
            (root / "README.md").write_text(
                update_catalog.START + "\n" + update_catalog.END + "\n")
            for name, data in {
                "task.toml": "[agent]\ntimeout_sec = 7200\n", "task.json": '{"example_count":1}', "instruction.md": "",
                "release.json": "{}", "provenance.json": "{}",
                "qualification-status.json": '{"task":"example","payload_status":"public-payload-missing"}',
                "payload.json": json.dumps({"files": [{
                    "scope": "public", "name": "blob", "destination": "environment/blob",
                    "bytes": 6, "sha256": hashlib.sha256(b"public").hexdigest()}]}),
            }.items():
                (task / name).write_text(data)
            artifacts = environment / "public-artifacts.json"
            artifacts.write_text(json.dumps({"files": {"qualification-status.json": "a" * 64}}))
            lock = environment / "source.lock.json"
            lock.write_text(json.dumps({"public_artifacts": "public-artifacts.json",
                                       "public_artifacts_sha256": "b" * 64}))
            stage_payload.stage(task, source)
            with patch.object(update_catalog, "ROOT", root), patch.object(sys, "argv", ["catalog", "--all"]):
                update_catalog.main()
                self.assertTrue(all(path.read_text() == text for path, text in update_catalog.generated().items()))
            status = task / "qualification-status.json"
            self.assertEqual(json.loads(status.read_text())["payload_status"], "ready")
            self.assertEqual(json.loads(artifacts.read_text())["files"]["qualification-status.json"],
                             hashlib.sha256(status.read_bytes()).hexdigest())
            self.assertEqual(json.loads(lock.read_text())["public_artifacts_sha256"],
                             hashlib.sha256(artifacts.read_bytes()).hexdigest())
