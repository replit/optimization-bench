"""Receipt-only v1.1 scoring labels; the frozen judge's execution stays untouched."""
import argparse
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "datasets/v1.1-gate-policy.json"


def task_gate_flags(task_id, root=ROOT):
    """Derive applicability from protected metadata and actual registration, not task names."""
    from judge.fuzzing_generic import task_runners
    if not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", task_id):
        raise ValueError("A valid task_id is required for task-scoped headline policy")
    task = Path(root) / "tasks" / task_id
    metadata = json.loads((task / "task.json").read_text())
    _, module = task_runners(task)
    timed_cli = metadata.get("timed_surface") == "public_cli"
    registered = callable(getattr(module, "CLI_CASES", None))
    return {"timed_surface": metadata.get("timed_surface", "not_declared_public_cli"),
            "cli_requests_registered": registered,
            "cli_faults_headline": timed_cli and registered}


def cli_projection(record, exemptions):
    """Project authenticated per-case classifications; never rerun or reclassify raw probes."""
    aggregate = status(record)
    if not isinstance(record, dict) or "cases" not in record:
        if aggregate in exemptions:
            return "not_applicable", [{"status": aggregate, "scope": "whole_matrix"}]
        return aggregate, []
    cases = record["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("CLI cases must be a nonempty authenticated classification list")
    eligible, reported = [], []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("CLI case must be a verifier record")
        original = case.get("original_status", case.get("baseline_status"))
        current = status(case)
        if (original in exemptions or current == "not_applicable" or
                original is None and current == "baseline_nonrepeatable"):
            reported.append(dict(case))
        else:
            eligible.append(current)
    failures = [value for value in eligible if value != "passed"]
    return failures[0] if failures else "passed" if eligible else "not_applicable", reported


def status(value):
    if value is None:
        return "unmeasured"
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise ValueError("Gate records must be status strings or verifier record objects")
    return value.get("status") or (
        "passed" if value.get("passed") is True else
        "failed" if value.get("passed") is False else "unmeasured")


def project(receipt, policy=None, root=ROOT):
    """Select headline gates from trusted receipts, never reinterpret probe execution."""
    policy = policy or json.loads(POLICY.read_text())
    if policy.get("schema") != "optimbench-v1.1-gate-policy-v1":
        raise ValueError("Unknown v1.1 gate policy")
    flags = task_gate_flags(receipt.get("task_id"), root)
    gates = receipt.get("gates", {})
    if not isinstance(gates, dict):
        raise ValueError("Expected a verifier gate-record mapping")
    hooks = set(policy["library_callbacks"] + policy["state_hooks"])
    registered = set(receipt.get("registered_hooks", []))
    if registered - hooks:
        raise ValueError("Unknown registered headline hook")
    # An observed hook finding is never hidden by an omitted registry label.
    observed = hooks & set(gates)
    active = registered | observed
    headline = {name: status(gates.get(name)) for name in policy["required_receipts"]}
    headline.update({name: status(gates.get(name)) for name in sorted(active)})
    cli_reported = []
    reported_names = list(policy["reported_only"])
    if flags["cli_faults_headline"]:
        headline["cli_faults"], cli_reported = cli_projection(
            gates.get("cli_faults"),
            set(policy["conditional_cli_gate"]["report_only_case_statuses"]))
    else:
        reported_names.append("cli_faults")
    blocking = [name for name, value in headline.items()
                if value != "passed" and not (
                    name in hooks | {"cli_faults"} and value == "not_applicable")]
    unmeasured = sorted((hooks - active) | {
        name for name, value in headline.items() if value == "unmeasured"})
    passed = not blocking
    score = receipt.get("optim_score")
    if score is not None and (
            type(score) not in (int, float) or not math.isfinite(score) or score < 0):
        raise ValueError("Provided OptimScore must be a finite nonnegative measurement")
    return {
        "schema": "optimbench-v1.1-headline-report-v1",
        "policy_id": policy["policy_id"],
        "task_id": receipt["task_id"],
        "task_gate_flags": flags,
        "frozen_judge_commit": policy["frozen_judge_commit"],
        "judge_revision": policy.get("judge_revision", "rc.1"),
        "execution_correctness_overlays": policy.get("execution_correctness_overlays", {}),
        "headline_status": "headline_passed" if passed else "headline_blocked",
        "headline_passed": passed,
        "headline_gates": headline,
        "blocking_gates": blocking,
        "reported_only": {name: status(gates.get(name)) for name in reported_names},
        "cli_reported_only_cases": cli_reported,
        "unmeasured_hooks": unmeasured,
        "optim_score": score if passed else None,
        "score_label": ("provided_measurement" if passed and score is not None
                        else "not_reported" if passed else "blocked_by_headline_gate"),
        "legacy_rc_status": receipt.get("legacy_rc_status"),
        "legacy_rc_aggregate_used": False,
        "new_measurements_performed": False,
        "raw_receipts_modified": False,
    }


def registry_gates(receipt, prerequisites, root=ROOT):
    """Supply only actual applicable v1.1 successes to the frozen registry API.

    In particular, a reported-only cpu_subsets/header/CLI failure is not smuggled
    into _gates_passed, which checks every supplied value. This does not waive
    registry fingerprint, code, qualification, scope or release-identity checks.
    """
    report = project(receipt, root=root)
    required = dict(prerequisites)
    required.update({name: value for name, value in report["headline_gates"].items()
                     if value != "not_applicable"})
    if any(value != "passed" for value in required.values()):
        raise ValueError("Applicable v1.1 prerequisite or headline gate did not pass")
    return list(required), required


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    print(json.dumps(project(json.loads(args.receipt.read_text())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
