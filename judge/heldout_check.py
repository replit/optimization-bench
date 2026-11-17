"""Correctness-only held-out source/build/upstream/output checks; never a timing run."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from . import build, gates, heldout, policy, public_input_preflight, score, telemetry, upstream, verifier, reference
from .leftovers import classification
from runner import Context, HarnessError, fingerprint, references, run_case
from docker_executor import DockerExecutor


@heldout.acquired
def compare(task, baseline, candidate, projects, image, cpuset, negative_control=False):
    """Compare exact protected outputs, including numeric tasks without tolerance."""
    task = Path(task).resolve()
    heldout.check(task / "tests", require_available=True)
    inputs = task / "tests/heldout"
    projects = heldout.project_root(task / "tests", projects)
    settings = json.loads((task / "tests/judge.json").read_text())
    module = verifier.load_task(task)
    rows = list(references(module, inputs))
    expected = {row["path"]: (row["expect_len"], row["expect_sha"]) for row in rows}
    cases = list(module.cases(inputs))
    if not expected or len(expected) != len(rows) or not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("Missing or duplicate held-out references/cases")
    scopes = {}
    for case in cases:
        names = (set(expected) if len(cases) == 1 else {case.id} if case.id in expected else
                 {doc["path"] for doc in case.data["docs"]} if "docs" in case.data else set())
        if not names or not names <= expected.keys():
            raise ValueError("Missing held-out reference scope: " + case.id)
        scopes[case.id] = names
    if set().union(*scopes.values()) != expected.keys():
        raise ValueError("Held-out cases do not cover every protected reference")
    records = []
    control_case = min(scopes) if negative_control else None
    control_output = None
    control_original_matched = False
    for case in cases:
        want = {name: expected[name] for name in scopes[case.id]}
        baseline_leftovers = None
        for arm, folder in (("baseline", baseline), ("candidate", candidate)):
            with tempfile.TemporaryDirectory(prefix="heldout-check-case-") as temporary, \
                    heldout.workload_view(task / "tests", task / "environment/workload") as workload:
                ctx = Context(Path(temporary), Path(folder).resolve(), inputs, Path(projects),
                              workload, seed=0, docker=True)
                executor = DockerExecutor(image, cpuset, memory=settings["memory"],
                    pids_limit=settings["pids_limit"], extra_docker_flags=verifier.HARDENING,
                    tmpfs="/tmp:rw,nosuid,nodev,size=2g", task=task)
                outputs, steps = run_case(module, case, ctx, executor)
                try:
                    leftover_classes = classification(steps)
                except ValueError as error:
                    error.heldout_failure_context = {
                        "stage": "rule-d-classification", "case": case.id, "arm": arm,
                        "run_case_returned": True,
                        "missing_snapshot_fields": "unavailable; no liveness inference",
                        "completed_record_count": len(records),
                        "last_completed_record": {key: records[-1][key] for key in
                            ("case", "build", "matched")} if records else None,
                        "steps": [{"step": index, "exit_code": result.exit_code,
                            "leftovers": result.leftovers,
                            "telemetry": {key: result.telemetry.get(key) for key in
                                ("execution_protocol", "main_pid", "exit_observation", "kernel_exiting_tasks")}}
                            for index, result in enumerate(steps)]}
                    raise
                if arm == "baseline":
                    baseline_leftovers = leftover_classes
                leftovers_matched = leftover_classes == baseline_leftovers
                # New resident workers must finish, even when pristine has the same leak.
                workers_finished = not any(row.get("kind") in ("thread", "process")
                                           for row in leftover_classes)
                if negative_control and arm == "candidate" and case.id == control_case:
                    if not outputs:
                        raise HarnessError("Negative control has no observed output to perturb")
                    original = {name: fingerprint(data, getattr(module, "RESULT_ENCODING", None))
                                for name, data in outputs.items()}
                    control_original_matched = original == want and leftovers_matched and workers_finished
                    # Verifier-owned control: never edit a source, fixture or
                    # expected result, and never reinterpret this as a candidate.
                    control_output = min(outputs)
                    outputs = {**outputs, control_output: bytes(outputs[control_output]) + b"\0"}
                observed = {name: fingerprint(data, getattr(module, "RESULT_ENCODING", None))
                            for name, data in outputs.items()}
                details = {name: {"matched": observed.get(name) == want.get(name),
                                  "expected": want.get(name), "observed": observed.get(name)}
                           for name in want.keys() | observed.keys()}
                records.append({"case": case.id,
                    "build": "control" if negative_control and arm == "candidate" else arm,
                    "matched": observed == want and leftovers_matched and workers_finished,
                    "outputs_matched": observed == want, "leftovers_matched": leftovers_matched,
                    "resident_workers_finished": workers_finished,
                    "leftover_classification": leftover_classes,
                    "outputs": details, "steps": [{"exit_code": r.exit_code,
                                                   "leftovers": r.leftovers} for r in steps]})
                if executor.container_prepare is not None:
                    records[-1]["container_prepare"] = executor.container_prepare
    baseline_failed = [r["case"] for r in records if r["build"] == "baseline" and not r["matched"]]
    failed = [r["case"] for r in records if r["build"] != "baseline" and not r["matched"]]
    result = {"status": "invalid run" if baseline_failed else "incorrect" if failed else "heldout check passed",
            "correct": not baseline_failed and not failed, "records": records,
            "failed_cases": failed, "baseline_failed_cases": baseline_failed}
    if negative_control:
        passed = (not baseline_failed and failed == [control_case] and control_output is not None
                  and control_original_matched)
        result.update(status="negative control rejected" if passed else "invalid control",
                      correct=False, is_control=True, candidate_verdict=None,
                      control_passed=passed, control_case=control_case, control_output=control_output,
                      control_original_matched=control_original_matched,
                      control_operation="append one NUL byte to one observed output")
    return result


@heldout.acquired
def run(task, image, cpuset, work, cache, source=None, artifacts=None, negative_control=False):
    task, work, cache = Path(task).resolve(), Path(work).resolve(), Path(cache).resolve()
    if source is not None and artifacts is not None:
        raise ValueError("Supply source or artifacts, not both")
    if negative_control and (source is not None or artifacts is not None):
        raise ValueError("Negative control must use pristine source, not candidate artifacts")
    # Authenticate before any build or gate, including fail-closed LFS checks.
    heldout.check(task / "tests", require_available=True)
    telemetry.pinned(cpuset)
    image = verifier.image_id(image)
    work.mkdir(parents=True, exist_ok=False)
    trusted = score.assets(task, image, cache)
    public_input_preflight.ensure_public_inputs(task, source=trusted / "public-workload")
    rebuilt_reference = reference.ensure(task, trusted, image, cpuset, cache)
    candidate = work / "candidate"
    mapping = score.artifacts(task, artifacts)[0] if artifacts is not None else None
    built = build.build(task, trusted / "pristine", image, cpuset, candidate,
                        artifacts=mapping, source=source if source is not None else
                        trusted / "pristine" if artifacts is None else None)
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    admission = policy.envelope(candidate / "source", trusted / "pristine", rules)
    receipt, source_sha = score.upstream_gate(
        task, trusted, candidate, image, {"cpuset": cpuset}, work, cache, rebuilt_reference)
    upstream.validate_receipt(receipt, task.name, image, source_sha,
                              verifier.build_inventory(candidate / "build"))
    result = compare(task, rebuilt_reference["build"], candidate / "build", trusted / "projects",
                     image, cpuset, negative_control=negative_control)
    result.update(mode="heldout-check", task=task.name, image=image, public_only=False,
                  claimable=False, timing_performed=False, aa_sampling_performed=False,
                  speedup=None, calibration_required=False,
                  heldout_lock_sha256=heldout.sha(task / "tests/heldout.lock.json"),
                  source_sha256=policy.source_digest(admission),
                  build_sha256=built["build_sha256"],
                  reference_key=rebuilt_reference["key"],
                  reference_build_sha256=rebuilt_reference["build_sha256"],
                  gates={"source_policy": "passed", "build": "passed", "upstream": "passed",
                         "heldout_exact_outputs": result["status"]})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, help="Authenticated image/upstream cache")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--source", type=Path, help="Complete candidate tree; default is pristine no-op")
    group.add_argument("--artifacts", type=Path, help="JSON mapping of editable source roots to exports")
    group.add_argument("--negative-control", action="store_true",
                       help="Pristine build plus verifier-owned output perturbation; never a candidate verdict")
    args = parser.parse_args(argv)
    work = args.output.with_name(args.output.stem + "-work")
    cache = args.cache or args.output.parent / "heldout-cache"
    try:
        result = run(args.task, args.image, args.cpuset, work, cache,
                     args.source, args.artifacts, args.negative_control)
    except reference.QualificationError as error:
        result = {"mode": "heldout-check", "status": "invalid run",
                  "task_qualification": "failed", "correct": None, "claimable": False,
                  "timing_performed": False, "aa_sampling_performed": False,
                  "speedup": None, "harness_error": str(error)}
    except gates.UpstreamRejected as error:
        rejection = args.output.with_name(args.output.stem + "-upstream-rejection.json")
        score.save(rejection, error.receipts)
        result = {"mode": "heldout-check", "status": "incorrect", "correct": False,
                  "claimable": False, "timing_performed": False, "aa_sampling_performed": False,
                  "speedup": None, "upstream_gate": "rejected",
                  "upstream_rejection": str(rejection), "reasons": [str(error)]}
        if args.negative_control:
            result.update(status="invalid control", is_control=True,
                          candidate_verdict=None, control_passed=False)
    except (HarnessError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        result = {"mode": "heldout-check", "status": "invalid run", "correct": False,
                  "claimable": False, "timing_performed": False, "aa_sampling_performed": False,
                  "speedup": None, "harness_error": str(error)}
        if hasattr(error, "heldout_failure_context"):
            result["failure_context"] = error.heldout_failure_context
        if args.negative_control:
            result.update(is_control=True, candidate_verdict=None, control_passed=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(result["status"])
    return 0 if result["correct"] else 1 if result["status"] in (
        "incorrect", "negative control rejected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
