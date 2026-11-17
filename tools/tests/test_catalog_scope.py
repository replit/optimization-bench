import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import update_catalog as catalog
import check_tasks


class CatalogScopeTests(unittest.TestCase):
    def fixture(self, root, name, *, lfs=False):
        task = root / "tasks" / name
        (task / "environment/workload/inputs").mkdir(parents=True)
        (task / "tests").mkdir()
        for filename, value in {
            "task.json": {"title": name, "example_count": 1},
            "release.json": {},
            "provenance.json": {"current_files_sha256": {}, "expected_output_coincidences": [{"existing": True}]},
            "qualification-status.json": {"payload_status": "ready"},
            "tests/heldout.lock.json": {"files": {}, "status": "unavailable"},
            "tests/judge.json": {},
        }.items():
            (task / filename).write_text(json.dumps(value))
        (task / "instruction.md").write_text("original instructions")
        (task / "task.toml").write_text("[agent]\ntimeout_sec=3600\n")
        body = b"fixture public body"
        entry = {"scope": "public", "name": "body", "destination": "environment/workload/inputs/body",
                 "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        if lfs:
            entry.update(kind="repo-lfs", source="public/files/body", reason="public recovery fixture")
            source = task / "public/files/body"
            source.parent.mkdir(parents=True)
            source.write_bytes(catalog.LFS_POINTER + b"oid sha256:" + b"a" * 64 + b"\nsize 19\n")
        else:
            (task / entry["destination"]).write_bytes(body)
        (task / "payload.json").write_text(json.dumps({"files": [entry]}))
        return task

    def root(self, directory):
        root = Path(directory)
        (root / "README.md").write_text(catalog.START + "\nold\n" + catalog.END)
        (root / "datasets").mkdir()
        return root

    def test_generation_for_one_task_never_opens_unavailable_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            first = self.fixture(root, "selected")
            other = self.fixture(root, "other", lfs=True)
            before = (other / "provenance.json").read_bytes()
            with patch.object(catalog, "ROOT", root), patch.object(catalog, "task_readmes", return_value=("task readme", "env readme")), patch.object(catalog, "output_coincidences") as collisions:
                result = catalog.generated([first])
            collisions.assert_not_called()
            self.assertTrue(result)
            self.assertTrue(all(path.is_relative_to(first) for path in result))
            self.assertEqual((other / "provenance.json").read_bytes(), before)
            provenance = json.loads(result[first / "provenance.json"])
            self.assertEqual(provenance["expected_output_coincidences"], [{"existing": True}])

    def test_global_missing_lfs_is_per_task_unavailable_not_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            first = self.fixture(root, "selected")
            other = self.fixture(root, "other", lfs=True)
            with patch.object(catalog, "ROOT", root), patch.object(catalog, "task_readmes", return_value=("task readme", "env readme")), patch.object(catalog, "output_coincidences", return_value=[]):
                _, ready, statuses, _ = catalog.catalog()
                output = catalog.generated()
            self.assertEqual(statuses[other]["payload_status"], "unavailable")
            self.assertIn("selected", ready)
            self.assertNotIn("other", ready)
            self.assertFalse(any(p.is_relative_to(other) for p in output))
            self.assertNotIn(root / "README.md", output)  # Global aggregates cannot be certified.

    def test_materialized_corruption_is_never_soft_unavailability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            task = self.fixture(root, "broken")
            (task / "environment/workload/inputs/body").write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "corruption"):
                catalog.unavailable_inputs(task)

    def test_repo_lfs_recovery_corruption_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            task = self.fixture(root, "broken", lfs=True)
            (task / "public/files/body").write_bytes(b"materialized but wrong")
            with self.assertRaisesRegex(ValueError, "corruption"):
                catalog.unavailable_inputs(task)

    def test_fetchable_setup_preserves_input_commitments(self):
        url = "https://example.org/public-source.csv"
        for route in ("direct", "transport", "operator"):
            with self.subTest(route=route), tempfile.TemporaryDirectory() as tmp:
                root = self.root(tmp)
                task = self.fixture(root, "selected")
                payload = json.loads((task / "payload.json").read_text())
                entry = payload["files"][0]
                if route == "direct":
                    entry["url"] = url
                elif route == "transport":
                    entry["transport"] = {
                        "url": url, "bytes": entry["bytes"], "sha256": entry["sha256"]}
                (task / "payload.json").write_text(json.dumps(payload))
                expected = "operator" if route == "operator" else "upstream"
                with patch.object(catalog, "ROOT", root):
                    catalog.normalize_distribution([task])
                    readme, _ = catalog.task_readmes(task, "ready")
                normalized = json.loads((task / "payload.json").read_text())["files"][0]
                self.assertEqual(normalized, {**entry, "distribution": expected})
                self.assertEqual("/path/to/original-public-packet" in readme, route == "operator")

    def test_task_selector_never_expands_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            first = self.fixture(root, "selected")
            self.fixture(root, "other")
            with patch.object(catalog, "ROOT", root):
                self.assertEqual(catalog.task_paths(["selected"]), [first])
                with self.assertRaises(ValueError):
                    catalog.task_paths(["../outside"])
                with self.assertRaises(ValueError):
                    catalog.task_paths(["selected", "selected"])

    def test_cli_write_requires_explicit_scope_and_does_not_touch_other_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            selected = self.fixture(root, "selected")
            other = self.fixture(root, "other", lfs=True)
            before = (other / "provenance.json").read_bytes()
            with patch.object(catalog, "ROOT", root), patch.object(catalog, "normalize_distribution") as normalize, patch.object(catalog, "refresh_bindings") as refresh, patch.object(catalog, "generated", return_value={selected / "provenance.json": '{"selected":true}\n'}), patch.object(sys, "argv", ["update_catalog.py", "--task", "selected"]):
                catalog.main()
            normalize.assert_called_once_with([selected])
            self.assertTrue(all(call.args == ([selected],) for call in refresh.call_args_list))
            self.assertEqual((other / "provenance.json").read_bytes(), before)
            with patch.object(sys, "argv", ["update_catalog.py"]), self.assertRaises(SystemExit):
                catalog.main()

    def test_task_check_catalog_scoped_but_unavailable_cross_audit_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            selected = self.fixture(root, "selected")
            other = self.fixture(root, "other", lfs=True)
            with patch.object(catalog, "ROOT", root), patch.object(check_tasks, "ROOT", root), patch.object(check_tasks, "generated", return_value={}) as generated, patch.object(check_tasks, "check_task", return_value={"task": "selected"}) as task_check, patch.object(check_tasks, "check_repository", return_value=[]) as repository, patch.object(sys, "argv", ["check_tasks.py", "--task", "selected"]), patch("builtins.print") as printed:
                check_tasks.main()
            generated.assert_called_once_with([selected])
            task_check.assert_called_once_with(selected, False)
            self.assertEqual(repository.call_args.args[1], [selected])
            result = json.loads(printed.call_args.args[0])
            self.assertEqual(result["disjointness"]["unavailable_tasks"], ["other"])
            self.assertFalse(result["disjointness"]["complete"])

    def test_selected_unavailable_cli_is_structured_nonzero_and_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            selected = self.fixture(root, "selected", lfs=True)
            other = self.fixture(root, "other")
            before = {p: p.read_bytes() for p in (selected / "provenance.json", other / "provenance.json", root / "README.md")}
            with patch.object(catalog, "ROOT", root), patch.object(sys, "argv", ["update_catalog.py", "--task", "selected", "--check"]), patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as error:
                    catalog.main()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(json.loads(printed.call_args.args[0])["status"], "partial-unavailable")
            for path, value in before.items():
                self.assertEqual(path.read_bytes(), value)

    def test_unavailable_public_transport_does_not_waive_reference_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            task = self.fixture(root, "selected", lfs=True)
            data = json.loads((task / "payload.json").read_text())
            data["files"].append({"scope": "heldout", "destination": "tests/heldout/files/reference",
                                  "retention": "fetch-only", "sha256": "a" * 64, "bytes": 1})
            (task / "payload.json").write_text(json.dumps(data))
            # This declaration has no matching lock reference. It is invalid,
            # not made "unavailable" by another missing public LFS body.
            with self.assertRaisesRegex(ValueError, "declarations differ"):
                catalog.unavailable_inputs(task)


if __name__ == "__main__":
    unittest.main()
