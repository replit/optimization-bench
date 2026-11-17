import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from judge import fuzzing_generic, fuzzing_io
from tools.heldout_policy import EMPTY_SHA256, empty_input_paths, private_hashes

TASK = Path(__file__).resolve().parents[2]


class RuffHookTests(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.hooks = fuzzing_generic.task_runners(TASK)

    def request(self, output):
        return self.hooks.CLI_CASES({"out": output})

    def test_both_stdout_sizes_and_all_mandatory_faults_are_registered(self):
        with tempfile.TemporaryDirectory() as output:
            request = self.request(output)
            reach = fuzzing_io.cli_reachable(request, TASK.name, self.hooks.CLI_PRECONDITION)
            self.assertEqual(reach["status"], "passed", reach)
            self.assertEqual(reach["reachable_credit"], ["large_stdout", "small_stdout"])
            for spec in request["cases"]:
                self.assertEqual(set(fuzzing_io.cli_case_faults(spec)),
                                 set(fuzzing_io.CLI_DEFAULT_FAULTS))
                argv = spec["step"]["argv"]
                self.assertTrue({"--isolated", "--no-cache", "--no-fix", "--exit-zero"} <= set(argv))
                self.assertEqual(spec["step"]["env"]["RAYON_NUM_THREADS"], "8")
                content = (Path(spec["inputs"]) / "sample.py").read_bytes()
                count = spec["case"]["input"]["lines"]
                self.assertEqual(content, "".join(
                    f"import os as unused_{index}\n" for index in range(count)).encode())
                self.assertEqual(len(set(content.splitlines())), count)

    def test_changed_fixture_or_contract_cannot_earn_fault_credit(self):
        with tempfile.TemporaryDirectory() as output:
            spec = self.request(output)["cases"][0]
            for key, value in (("task", "another-task"), ("options", {}), ("input", None),
                               ("input", []),
                               ("input", {"fixture": "arbitrary", "lines": 1, "sha256": "0" * 64})):
                case = copy.deepcopy(spec["case"])
                case[key] = value
                self.assertFalse(self.hooks.CLI_PRECONDITION(case, spec["surface"])["valid"])

    def test_binary_only_exclusions_are_bound_to_protected_recipe_and_pin(self):
        lock = json.loads((TASK / "environment/source.lock.json").read_text())
        self.assertEqual(set(self.callbacks), {"stream_faults", "repeat_in_process", "aliasing"})
        for callback in self.callbacks.values():
            result = callback({})
            self.assertEqual(result["status"], "not_applicable")
            self.assertEqual(result["applicability_evidence"]["upstream_commit"],
                             lock["ruff"]["commit"])
        result = fuzzing_io.header_gate({"hooks": self.hooks})
        self.assertEqual(result["status"], "not_applicable")

    def test_library_artifact_scope_cannot_inherit_binary_exclusions(self):
        original = Path.read_text

        def changed(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == TASK / "tests/judge.json":
                settings = json.loads(text)
                settings["public_interface"]["kind"] = "rust"
                return json.dumps(settings)
            return text

        with patch.object(Path, "read_text", changed):
            with self.assertRaisesRegex(ValueError, "public binary scope changed"):
                self.hooks.HEADER_CONSUMERS({})


class RuffHeldoutEditionTests(unittest.TestCase):
    def assert_case_bytes(self, path, digest, size):
        body = path.read_bytes()
        if body.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            self.assertEqual(body, (
                "version https://git-lfs.github.com/spec/v1\n"
                f"oid sha256:{digest}\nsize {size}\n").encode())
            return None
        self.assertEqual((len(body), hashlib.sha256(body).hexdigest()), (size, digest))
        return body

    def test_original_public_inputs_and_protected_adapter_are_unchanged(self):
        pins = {
            # v1.0 command-matrix revision (2026-11-16): new case documents and outputs.
            "environment/workload/inputs/manifest.json": "f2bfe6293ff34f6abe75be186d980ae6a4277d791926cf44ab93968d6e2296b6",
            "environment/workload/task.py": "7bc502067f42bfb75f0f47e76e1dd47b58f4119b1cb5d8ec75140c9a23d64254",
            "environment/workload/runner.py": "78720e32703d9d24624d81f1ebe260c2a40e871d830bd63d08ce6c839cdc76c6",
            "environment/workload/build.sh": "d6c560f2bd1b5fb3e5261897953d73835512372207bf11dc016978c67d61c240",
        }
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((TASK / name).read_bytes()).hexdigest(), digest, name)

    def test_complete_nonempty_index_has_no_extra_empty_exemptions(self):
        inventory = json.loads((TASK / "tests/heldout/project-inventories.json").read_text())
        index = json.loads((TASK / "tests/heldout/inputs.lock.json").read_text())["files"]
        expected = {f"projects/{name}/{path}": {key: row[key] for key in ("bytes", "sha256")}
                    for name, project in inventory["projects"].items()
                    for path, row in project["files"].items() if row["bytes"]}
        self.assertEqual(index, expected)
        self.assertEqual(len(index), 644)
        self.assertEqual(sum(len(row["files"]) for row in inventory["projects"].values()), 651)
        self.assertEqual(sum(not row["bytes"] for project in inventory["projects"].values()
                             for row in project["files"].values()), 7)
        guarded = private_hashes(TASK)
        self.assertLessEqual({row["sha256"] for row in index.values()}, guarded)
        self.assertNotIn(EMPTY_SHA256, guarded)
        self.assertEqual(empty_input_paths(TASK), set())

    def test_new_cases_and_expectations_match_the_single_authoring_record(self):
        manifest = json.loads((TASK / "tests/heldout/manifest.json").read_text())
        edition = json.loads((TASK / "tests/heldout/edition.json").read_text())
        docs = [doc for batches in manifest["splits"].values() for batch in batches for doc in batch["docs"]]
        expected = {
            "httpx": (281, "7128553a4cfacb56aa5c90046e828f6ddff04177626ef1c88540a30e87f6585c", 0),
            "rich": (1471265, "b93122927e0395047bdf22d35c0ec4c491bd4474437381f9459a78387508d610", 1),
        }
        observations = {row["case"]: row for row in edition["authoring"]["observations"]}
        self.assertEqual({doc["path"] for doc in docs}, set(expected))
        revision = edition["command_matrix_revision"]["outputs"]
        for doc in docs:
            size, digest, code = expected[doc["path"]]
            # Since the v1.0 command-matrix revision, cases run 14 commands; the original
            # single-command 0.3.0 authoring record below is kept as history.
            self.assertEqual((doc["expect_len"], doc["expect_sha"]),
                             (revision[doc["path"]]["bytes"], revision[doc["path"]]["sha256"]))
            self.assertEqual(len(json.loads((TASK / "tests/heldout/files" / doc["sha256"]).read_text())["commands"]), 14)
            self.assertEqual(observations[doc["path"]]["command"],
                             ["/app/build/ruff", "check", "--no-cache", "--no-fix", "--output-format=json", "."])
            self.assertEqual(observations[doc["path"]]["inventory_sha256"], json.loads(
                (TASK / "tests/heldout/project-inventories.json").read_text())["projects"][doc["path"]]["inventory_sha256"])
            self.assertEqual(observations[doc["path"]]["exit_code"], code)
            self.assertEqual(observations[doc["path"]]["output"], {"bytes": size, "sha256": digest})
        self.assertEqual(edition["authoring"]["pass_count"], 1)
        self.assertEqual(edition["authoring"]["timing_samples"], 0)
        self.assertIs(edition["authoring"]["qualification"], False)
        self.assertIs(edition["authoring"]["owned_cleanup_verified"], True)

    def test_materialized_case_bodies_match_the_frozen_commands_and_inventory(self):
        manifest = json.loads((TASK / "tests/heldout/manifest.json").read_text())
        inventories = json.loads((TASK / "tests/heldout/project-inventories.json").read_text())["projects"]
        unavailable = []
        for batches in manifest["splits"].values():
            for batch in batches:
                for doc in batch["docs"]:
                    body = self.assert_case_bytes(
                        TASK / "tests/heldout/files" / doc["sha256"], doc["sha256"], doc["bytes"])
                    if body is None:
                        unavailable.append(doc["path"])
                        continue
                    case = json.loads(body)
                    self.assertEqual(case["project"], doc["path"])
                    # The original command stays first; the v1.0 command-matrix revision adds
                    # `--select ALL --statistics` (stable and --preview) for py39-py314 and `format --check`.
                    versions = ["py39", "py310", "py311", "py312", "py313", "py314"]
                    matrix = [["check", "--no-cache", "--no-fix", "--output-format=json", "."]]
                    for version in versions:
                        matrix.append(["check", "--no-cache", "--no-fix", "--select", "ALL", "--statistics",
                                       "--target-version", version, "."])
                        matrix.append(["check", "--no-cache", "--no-fix", "--select", "ALL", "--preview",
                                       "--statistics", "--target-version", version, "."])
                    matrix.append(["format", "--no-cache", "--check", "."])
                    self.assertEqual(case["commands"], matrix)
                    self.assertEqual(case["inventory_sha256"], inventories[doc["path"]]["inventory_sha256"])
        if unavailable:
            self.skipTest("Unhydrated LFS case bodies; pointer commitments checked, body verification pending: "
                          + ", ".join(unavailable))

    def test_case_transport_rejects_corrupt_pointer_commitments(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic-input"
            pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 9\n"
            path.write_text(pointer)
            self.assertIsNone(self.assert_case_bytes(path, "a" * 64, 9))
            for value in (pointer.replace("a" * 64, "b" * 64),
                          pointer.replace("size 9", "size 8"), pointer + "extra\n",
                          pointer.replace("/spec/v1", "/spec/v2")):
                with self.subTest(pointer=value):
                    path.write_text(value)
                    with self.assertRaises(AssertionError):
                        self.assert_case_bytes(path, "a" * 64, 9)

    def test_case_transport_checks_materialized_bytes_without_skipping_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic-input"
            body = b"synthetic transport check\n"
            digest = hashlib.sha256(body).hexdigest()
            path.write_bytes(body)
            self.assertEqual(self.assert_case_bytes(path, digest, len(body)), body)
            for invalid in (body + b"x", b"S" + body[1:]):
                path.write_bytes(invalid)
                with self.assertRaises(AssertionError):
                    self.assert_case_bytes(path, digest, len(body))
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                self.assert_case_bytes(path, digest, len(body))

    def test_unhydrated_case_cannot_hide_missing_or_corrupt_materialized_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            heldout = task / "tests/heldout"
            (heldout / "files").mkdir(parents=True)
            pending = {"path": "synthetic-pending", "bytes": 9, "sha256": "a" * 64}
            (heldout / "files" / pending["sha256"]).write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 9\n")
            (heldout / "project-inventories.json").write_text(json.dumps({
                "projects": {"synthetic-present": {"inventory_sha256": "c" * 64}}}))
            case = {
                "project": "synthetic-present",
                "commands": json.loads((TASK / "tests/heldout/files" / json.loads(
                    (TASK / "tests/heldout/manifest.json").read_text())["splits"]["heldout"][0]["docs"][0]["sha256"]
                ).read_text())["commands"],  # the frozen v1.0 command matrix
                "inventory_sha256": "c" * 64,
            }
            values = [
                (case, unittest.SkipTest),
                ({**case, "project": "different-project"}, AssertionError),
                ({**case, "commands": [["--fix"]]}, AssertionError),
                ({**case, "inventory_sha256": "d" * 64}, AssertionError),
                (None, json.JSONDecodeError),
            ]
            with patch(__name__ + ".TASK", task):
                for value, error in values:
                    body = json.dumps(value).encode() if value is not None else b"not-json"
                    doc = {"path": "synthetic-present", "bytes": len(body),
                           "sha256": hashlib.sha256(body).hexdigest()}
                    path = heldout / "files" / doc["sha256"]
                    path.write_bytes(body)
                    manifest = {"splits": {"heldout": [{"docs": [pending, doc]}]}}
                    (heldout / "manifest.json").write_text(json.dumps(manifest))
                    with self.subTest(error=error.__name__, case=value):
                        with self.assertRaises(error):
                            self.test_materialized_case_bodies_match_the_frozen_commands_and_inventory()
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    self.test_materialized_case_bodies_match_the_frozen_commands_and_inventory()
                body = json.dumps(case).encode()
                doc = {"path": "synthetic-present", "bytes": len(body),
                       "sha256": hashlib.sha256(body).hexdigest()}
                (heldout / "files" / doc["sha256"]).write_bytes(body)
                (heldout / "manifest.json").write_text(json.dumps({
                    "splits": {"heldout": [{"docs": [doc]}]}}))
                self.test_materialized_case_bodies_match_the_frozen_commands_and_inventory()

    def test_fetch_only_histories_match_payload_and_never_stage_project_bodies(self):
        lock = json.loads((TASK / "tests/heldout.lock.json").read_text())
        payload = json.loads((TASK / "payload.json").read_text())
        rows = {row["destination"]: row for row in payload["files"]}
        self.assertEqual(set(lock["references"]), {"heldout/projects/httpx", "heldout/projects/rich"})
        for name, reference in lock["references"].items():
            row = rows["tests/" + name]
            self.assertEqual(reference["kind"], "git-history")
            self.assertEqual(reference["ref_policy"], "one-detached-commit-closure")
            self.assertEqual(row["retention"], "fetch-only")
            self.assertFalse((TASK / "tests" / name).exists())
            for key, value in reference.items():
                self.assertEqual(row[key], value, (name, key))
        for name, digest in lock["files"].items():
            row = rows["tests/" + name]
            self.assertEqual(row["sha256"], digest, name)
            self.assertIsNotNone(self.assert_case_bytes(TASK / "tests" / name, digest, row["bytes"]),
                                 "Authored case documents must be available without Git LFS")


if __name__ == "__main__":
    unittest.main()
