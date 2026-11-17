import json
from pathlib import Path
import tempfile
import unittest
import hashlib
from unittest.mock import patch

from tools import judge_command_audit as audit


class CommandAuditTests(unittest.TestCase):
    def test_agent_packet_and_export_entrypoints_are_explicitly_audited(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            names = ("public_runtime_packet.py", "export_source_snapshot.py",
                     "export_candidate_source.py", "prepare_verifier_assets.py")
            for name in names:
                (root / "tools" / name).write_text(
                    "import subprocess\nsubprocess.run(['git','status'])\n"
                    "def dynamic(argv):\n subprocess.run(argv)\n")
            sources = {path.relative_to(root).as_posix() for path in audit.sources(root)}
            for name in names:
                self.assertIn("tools/" + name, sources)
                self.assertEqual({row["execution_context"] for row in audit.inspect_source(
                    root / "tools" / name, root)}, {"host"})

    def fixture(self, root):
        (root / "judge").mkdir()
        (root / "tools/runner").mkdir(parents=True)
        (root / "tasks").mkdir()
        path = root / "judge/example.py"
        path.write_text("import subprocess\nsubprocess.run(['docker','info'])\n")
        return path

    def test_every_literal_and_dynamic_launch_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture(root)
            path.write_text("import subprocess\n"
                            "def first():\n cmd=['docker','info']; subprocess.run(cmd)\n"
                            "def second():\n cmd=['nm','artifact']; subprocess.run(cmd)\n"
                            "def dynamic(argv):\n subprocess.Popen(argv)\n")
            rows = audit.inventory(root)["process_sites_and_producers"]
            self.assertEqual([(r["function"], r["binary"]) for r in rows],
                             [("first", "docker"), ("second", "nm"), ("dynamic", None)])
            self.assertTrue(rows[-1]["dynamic"])

    def test_changed_producer_fails_until_explicit_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture(root)
            manifest = root / "tools/judge_external_commands.json"
            manifest.write_text(json.dumps(audit.inventory(root)))
            self.assertEqual(audit.check(root)["launch_sites_and_producers"], 1)
            path.write_text("import subprocess\nsubprocess.run(['unexpected-tool'])\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                audit.check(root)

    def test_no_raw_argv_secrets_printed_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture(root)
            path.write_text("import subprocess\nsubprocess.run(['docker','--private-token=SECRET'])\n")
            encoded = json.dumps(audit.inventory(root))
            self.assertNotIn("SECRET", encoded)
            self.assertIn("argv_expression_sha256", encoded)

    def test_missing_audit_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing"):
                audit.check(Path(tmp))

    def test_argument_identity_is_source_based_not_interpreter_ast_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture(root)
            with patch.object(audit.ast, "dump", side_effect=AssertionError("AST repr is version-dependent")):
                row = audit.inventory(root)["process_sites_and_producers"][0]
            self.assertEqual(row["argv_expression_sha256"], hashlib.sha256(b"['docker','info']").hexdigest())
            self.assertEqual(audit.inventory(root)["argv_identity_scheme"], "exact-source-segment-utf8-lf-v2")

    def test_native_surface_demangler_is_a_host_binutils_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture(root)
            path.write_text("import subprocess\nsubprocess.run(['c++filt','-n','_Z3foov'])\n")
            row = audit.inventory(root)["process_sites_and_producers"][0]
            self.assertEqual(row["binary"], "c++filt")
            self.assertEqual(row["execution_context"], "host")
            self.assertIn("binutils", audit.HOST["c++filt"])

    def test_task_setup_native_and_nested_helpers_are_discovered_without_fixture_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            task = root / "tasks/example"
            included = ("setup.py", "environment/bootstrap.py", "tests/acquire_payload.py",
                        "tests/upstream.py", "tests/native/deep/helper.py",
                        "tests/differential/conformance.py")
            excluded = ("tests/test_native.py", "tests/native/test_helper.py",
                        "tests/fixtures/tool.py", "tests/native/fixture/tool.py",
                        "tests/native/testdata/tool.py", "tests/differential/data/tool.py",
                        "environment/workload/inputs/tool.py", "tests/heldout/files/tool.py",
                        "tests/heldout/expected/tool.py", "tests/receipts/tool.py",
                        "tests/__pycache__/tool.py")
            for name in (*included, *excluded):
                path = task / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("import subprocess\nsubprocess.run(['unlisted-tool'])\n")
            (task / "tests/linked.py").symlink_to(task / "setup.py")
            actual = {path.relative_to(task).as_posix()
                      for path in audit.sources(root) if path.is_relative_to(task)}
            self.assertEqual(actual, set(included))

    def test_explicit_task_host_and_worker_contexts_do_not_hide_unknown_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            path = root / "tasks/example/tests/differential/conformance.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "import subprocess\n"
                "EXTERNAL_COMMAND_CONTEXTS={'acquire':'host-generator','verify':'pinned-worker'}\n"
                "def acquire():\n subprocess.run(['make','oracle'])\n"
                "def verify(argv):\n subprocess.run(argv)\n"
                "def undeclared():\n subprocess.run(['unlisted-tool'])\n")
            rows = audit.inspect_source(path, root)
            self.assertEqual([(row["binary"], row["execution_context"]) for row in rows], [
                ("make", "host-generator"), (None, "pinned-worker"),
                ("unlisted-tool", "task-helper-context-requires-review")])
            self.assertNotIn("make", audit.HOST)
            self.assertNotIn("unlisted-tool", audit.HOST)
            manifest = root / "tools/judge_external_commands.json"
            manifest.write_text(json.dumps(audit.inventory(root)))
            path.write_text(path.read_text().replace("'host-generator'", "'pinned-worker'"))
            with self.assertRaisesRegex(ValueError, "changed"):
                audit.check(root)

    def test_declared_input_programs_are_not_launchers_but_undeclared_pointers_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            task = root / "tasks/example"
            data = task / "tests/heldout/projects/input.py"
            data.parent.mkdir(parents=True)
            data.write_text("version https://git-lfs.github.com/spec/v1\n"
                            "oid sha256:" + "a" * 64 + "\nsize 123\n")
            helper = task / "tests/native/helper.py"
            helper.parent.mkdir()
            helper.write_text("import subprocess\nsubprocess.run(['python3','worker'])\n")
            payload = task / "payload.json"
            payload.write_text(json.dumps({"files": [{
                "destination": data.relative_to(task).as_posix(), "scope": "heldout",
                "bytes": 123, "sha256": "a" * 64}]}))
            result = audit.inventory(root)
            self.assertNotIn(data.relative_to(root).as_posix(), result["source_sha256"])
            self.assertIn(helper.relative_to(root).as_posix(), result["source_sha256"])
            self.assertEqual(result["source_sha256"]["tasks/example/payload.json"],
                             hashlib.sha256(payload.read_bytes()).hexdigest())
            self.assertIn("python3", [row["binary"] for row in result["process_sites_and_producers"]])
            payload.write_text(json.dumps({"files": []}))
            with self.assertRaises(SyntaxError):
                audit.inventory(root)

    def test_changed_input_declaration_requires_explicit_audit_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            task = root / "tasks/example"
            task.mkdir()
            payload = task / "payload.json"
            value = {"files": [{"destination": "tests/heldout/input.bin", "scope": "heldout",
                                 "bytes": 1, "sha256": "a" * 64}]}
            payload.write_text(json.dumps(value))
            manifest = root / "tools/judge_external_commands.json"
            manifest.write_text(json.dumps(audit.inventory(root)))
            value["files"][0]["sha256"] = "b" * 64
            payload.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "changed"):
                audit.check(root)

    def test_input_declarations_do_not_expand_to_prefixes_or_cousins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            task = root / "tasks/example"
            helper = task / "tests/native/helper.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("import subprocess\nsubprocess.run(['python3','worker'])\n")
            for destination in ("tests/native", "tests/native/helper",
                                "tests/native/helper.py/child", "tests/native/helper.py.bak",
                                "tests/native-cousin/helper.py"):
                with self.subTest(destination=destination):
                    (task / "payload.json").write_text(json.dumps({"files": [
                        {"destination": destination, "scope": "heldout"}]}))
                    self.assertIn(helper, audit.sources(root))

    def test_input_declarations_reject_noncanonical_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            task = root / "tasks/example"
            task.mkdir()
            for destination in ("../judge/example.py", "/judge/example.py",
                                "tests/../helper.py", "tests//helper.py",
                                "tests/./helper.py", "tests/helper.py/",
                                "tests\\helper.py", ".", ""):
                with self.subTest(destination=destination):
                    (task / "payload.json").write_text(json.dumps({"files": [
                        {"destination": destination, "scope": "heldout"}]}))
                    with self.assertRaisesRegex(ValueError, "normalized"):
                        audit.inventory(root)

    def test_bound_helper_keeps_source_precedence_over_an_input_declaration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            task = root / "tasks/example"
            helper = task / "tests/heldout/projects/helper.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("import subprocess\nsubprocess.run(['python3','worker'])\n")
            payload = task / "payload.json"
            payload.write_text(json.dumps({"files": []}))
            manifest = root / "tools/judge_external_commands.json"
            manifest.write_text(json.dumps(audit.inventory(root)))
            payload.write_text(json.dumps({"files": [
                {"destination": helper.relative_to(task).as_posix(), "scope": "heldout"}]}))
            self.assertIn(helper, audit.sources(root))
            with self.assertRaisesRegex(ValueError, "changed"):
                audit.check(root)
            manifest.write_text(json.dumps(audit.inventory(root)))
            self.assertIn(helper.relative_to(root).as_posix(),
                          audit.inventory(root)["source_sha256"])
            helper.write_text("version https://git-lfs.github.com/spec/v1\n"
                              "oid sha256:" + "a" * 64 + "\nsize 123\n")
            with self.assertRaises(SyntaxError):
                audit.inventory(root)

    def test_invalid_task_context_is_rejected_without_evaluating_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            path = root / "tasks/example/tests/acquire.py"
            path.parent.mkdir(parents=True)
            path.write_text("EXTERNAL_COMMAND_CONTEXTS={'acquire':'trust-any-binary'}\n")
            with self.assertRaisesRegex(ValueError, "Invalid task"):
                audit.inspect_source(path, root)
            path.write_text("EXTERNAL_COMMAND_CONTEXTS=__import__('does_not_exist')\n")
            with self.assertRaises((ValueError, TypeError)):
                audit.inspect_source(path, root)


if __name__ == "__main__":
    unittest.main()
