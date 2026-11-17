import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.v11_gate_policy import project, task_gate_flags


def receipt():
    return {
        "task_id": "numpy-quantile",
        "optim_score": 1.25,
        "legacy_rc_status": "generic_gates_pending",
        "registered_hooks": ["stream_faults", "repeat_in_process", "aliasing",
                             "process_state", "lifecycle"],
        "gates": {name: "passed" for name in (
            "exact_public_outputs", "exact_heldout_outputs", "gcp_aa_intel", "gcp_aa_amd",
            "stream_faults", "repeat_in_process", "aliasing", "process_state", "lifecycle")},
    }


class HeadlinePolicyTests(unittest.TestCase):
    def test_header_cli_and_cpu_failures_cannot_fail_headline_or_change_score(self):
        original = receipt()
        expected = project(original)
        for gate in ("header_consumers", "cli_faults", "cpu_correctness", "cpu_subsets"):
            for state in ("contract_regression", "invalid_run", "pending", "needs_review"):
                row = copy.deepcopy(original)
                row["gates"][gate] = {"status": state, "passed": False}
                before = copy.deepcopy(row)
                result = project(row)
                self.assertTrue(result["headline_passed"], (gate, state))
                self.assertEqual(result["optim_score"], expected["optim_score"])
                self.assertEqual(result["reported_only"][gate], state)
                self.assertEqual(row, before)

    def test_each_required_receipt_still_blocks(self):
        for gate in ("exact_public_outputs", "exact_heldout_outputs", "gcp_aa_intel", "gcp_aa_amd"):
            row = receipt()
            row["gates"].pop(gate)
            result = project(row)
            self.assertFalse(result["headline_passed"])
            self.assertIsNone(result["optim_score"])
            self.assertIn(gate, result["blocking_gates"])

    def test_registered_callback_and_actual_runtime_regressions_block(self):
        for gate in ("stream_faults", "repeat_in_process", "aliasing", "process_state", "lifecycle"):
            row = receipt()
            row["gates"][gate] = "contract_regression"
            self.assertFalse(project(row)["headline_passed"], gate)

    def test_absent_hooks_are_unmeasured_not_score_blockers(self):
        row = receipt()
        for gate in row.pop("registered_hooks"):
            row["gates"].pop(gate)
        result = project(row)
        self.assertTrue(result["headline_passed"])
        self.assertEqual(len(result["unmeasured_hooks"]), 5)

    def test_missing_registered_proof_is_not_a_pass(self):
        row = receipt()
        row["gates"].pop("aliasing")
        self.assertFalse(project(row)["headline_passed"])
        self.assertIn("aliasing", project(row)["unmeasured_hooks"])

    def test_observed_runtime_failure_cannot_be_hidden_by_registry_omission(self):
        row = receipt()
        row["registered_hooks"].remove("process_state")
        row["gates"]["process_state"] = "needs_review"
        self.assertFalse(project(row)["headline_passed"])

    def cli_task(self, registered=True, timed_surface="public_cli"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        task = root / "tasks/sqlfmt-cli"
        task.mkdir(parents=True)
        (task / "task.json").write_text(json.dumps({"timed_surface": timed_surface}))
        if registered:
            fuzz = task / "tests/fuzz"
            fuzz.mkdir(parents=True)
            (fuzz / "rc_hooks.py").write_text("def CLI_CASES(context): return {}\n")
        row = receipt()
        row["task_id"] = "sqlfmt-cli"
        return root, row

    def test_registered_cli_rewrite_and_flush_regressions_block(self):
        # Trusted historical classifications, not a new candidate execution campaign.
        row = receipt()
        row["task_id"] = "git-history-change-summary"
        for name in ("haiku_file_rewrite", "sonnet_flush"):
            row["gates"]["cli_faults"] = {
                "status": "contract_regression",
                "cases": [{"id": name, "original_status": "passed",
                           "status": "contract_regression"}]}
            result = project(row)
            self.assertTrue(result["task_gate_flags"]["cli_faults_headline"])
            self.assertFalse(result["headline_passed"], name)
            self.assertIn("cli_faults", result["blocking_gates"])
            self.assertIsNone(result["optim_score"])

    def test_actual_cli_timed_registered_tasks_are_derived_as_headline(self):
        for task_id in ("git-history-change-summary",):
            self.assertTrue(task_gate_flags(task_id)["cli_faults_headline"], task_id)

    def test_header_and_cpu_failures_still_do_not_veto_cli_timed_task(self):
        row = receipt()
        row["task_id"] = "git-history-change-summary"
        row["gates"]["cli_faults"] = "passed"
        for gate in ("header_consumers", "cpu_correctness", "cpu_subsets"):
            row["gates"][gate] = "contract_regression"
        self.assertTrue(project(row)["headline_passed"])

    def test_original_nonrepeatable_and_na_cli_cases_remain_reported(self):
        root, row = self.cli_task()
        row["gates"]["cli_faults"] = {
            "status": "contract_regression",
            "cases": [
                {"id": "unstable_original", "original_status": "baseline_nonrepeatable",
                 "status": "contract_regression"},
                {"id": "na", "status": "not_applicable"},
                {"id": "reliable", "original_status": "passed", "status": "passed"}]}
        before = copy.deepcopy(row)
        result = project(row, root=root)
        self.assertTrue(result["headline_passed"])
        self.assertEqual(result["optim_score"], 1.25)
        self.assertEqual(len(result["cli_reported_only_cases"]), 2)
        self.assertEqual(row, before)

    def test_nonrepeatable_aggregate_does_not_hide_reliable_case_failure(self):
        root, row = self.cli_task()
        row["gates"]["cli_faults"] = {
            "status": "baseline_nonrepeatable",
            "cases": [{"original_status": "baseline_nonrepeatable", "status": "failed"},
                      {"original_status": "passed", "status": "contract_regression"}]}
        self.assertFalse(project(row, root=root)["headline_passed"])

    def test_matrix_baseline_nonrepeatable_is_reported_only(self):
        root, row = self.cli_task()
        row["gates"]["cli_faults"] = "baseline_nonrepeatable"
        self.assertTrue(project(row, root=root)["headline_passed"])

    def test_library_timed_cli_failure_does_not_block(self):
        root, row = self.cli_task(timed_surface="library")
        row["gates"]["cli_faults"] = "contract_regression"
        result = project(row, root=root)
        self.assertFalse(result["task_gate_flags"]["cli_faults_headline"])
        self.assertTrue(result["headline_passed"])
        self.assertEqual(result["reported_only"]["cli_faults"], "contract_regression")

    def test_cli_timed_without_requests_is_report_only_not_inherited_pass(self):
        root, row = self.cli_task(registered=False)
        row["gates"]["cli_faults"] = "contract_regression"
        result = project(row, root=root)
        self.assertTrue(result["headline_passed"])
        self.assertFalse(result["task_gate_flags"]["cli_requests_registered"])

    def test_missing_registered_cli_proof_blocks(self):
        root, row = self.cli_task()
        result = project(row, root=root)
        self.assertFalse(result["headline_passed"])
        self.assertIn("cli_faults", result["blocking_gates"])

    def test_applicability_comes_from_metadata_not_task_name_or_receipt_flag(self):
        root, row = self.cli_task(timed_surface="library")
        row["cli_faults_headline"] = True
        row["gates"]["cli_faults"] = "failed"
        self.assertTrue(project(row, root=root)["headline_passed"])
        self.assertFalse(task_gate_flags("sqlfmt-cli", root)["cli_faults_headline"])

    def test_missing_task_identity_cannot_bypass_conditional_cli_gate(self):
        row = receipt()
        row.pop("task_id")
        with self.assertRaises(ValueError):
            project(row)
