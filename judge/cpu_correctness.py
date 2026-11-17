"""Untimed RC replays on physical subsets of the reserved judge CPUs.

No cases are generated here: the protected generic callbacks own the corpus,
worker path, admission and comparator. Limits are fail-closed, never sampling.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import signal
import threading

from . import cpu_policy, fuzzing_contract as contract, fuzzing_generic as generic

ARMS = ("baseline", "candidate")
# Same library-first ordering as generic.check: conditional driver exclusions
# consume the actual covering results, not registration or a second execution.
GATES = tuple(name for name in generic.GATES if name not in ("process_state", "cli_faults")) + ("cli_faults",)
DEFAULT_LIMITS = {"cases_per_gate": 256, "observations_per_arm": 4096,
                  "seconds_per_subset": 900}


class MissingEvidence(ValueError):
    pass


class InvalidEvidence(ValueError):
    pass


class SubsetTimeout(BaseException):
    """Cannot be swallowed by a task callback's ordinary infrastructure handler."""


def _bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _save(path, value):
    data = _bytes(value)
    path.write_bytes(data)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)}


@contextmanager
def _deadline(seconds):
    # Do not steal a caller's alarm or pretend a thread-local watchdog works.
    if threading.current_thread() is not threading.main_thread():
        raise MissingEvidence("CPU replay requires the main thread for its hard deadline")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise MissingEvidence("CPU replay cannot replace an active caller deadline")
    def expired(*_):
        raise SubsetTimeout("CPU subset runtime limit exceeded")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _affinity(value, cpus):
    if value is None:
        raise MissingEvidence("In-process sched_getaffinity observation missing")
    if (not isinstance(value, list) or any(type(cpu) is not int for cpu in value)
            or sorted(value) != cpus):
        raise InvalidEvidence("Actual affinity differs from requested physical CPUs")
    return value


def _normalize(name, result, context):
    """Extract individual executions, not a requested mask or a summary boolean."""
    evidence = result.get("evidence", {})
    cpus = context["cpu_requested"]
    runs, repros = [], []

    def add(arm, key, value, affinity):
        _affinity(affinity, cpus)
        runs.append({"arm": arm, "run_id": key, "actual_cpus": affinity,
                     "observation_sha256": hashlib.sha256(_bytes(value)).hexdigest()})

    def difference(key, left, right, equal):
        if not equal:
            repros.append({"gate": name, "run_id": key, "requested_cpus": cpus,
                           "baseline": left, "candidate": right})

    if name == "cli_faults":
        from .fuzzing_io import cli_semantic
        from .fuzzing_outputs import equal_cli
        from .output_contract import load_output_comparator
        try:
            policy = load_output_comparator(context["task"])
        except (OSError, ValueError) as error:
            raise MissingEvidence("CLI output-comparator declaration unavailable") from error
        if evidence.get("output_comparator") != policy:
            raise MissingEvidence("CLI evidence does not bind the declared output comparator")
        cases = evidence.get("cases", [])
        for case in cases:
            if case.get("status") == "out_of_contract":
                continue
            spec = case.get("comparison_spec")
            if not isinstance(spec, dict) or any(
                    not isinstance(spec.get(key), list) or any(not isinstance(path, str) for path in spec[key])
                    for key in ("required_outputs", "output_dirs", "watch_inputs")):
                raise MissingEvidence("CLI declared path-role evidence unavailable")
            replays = case.get("runs", [])
            if len(replays) != 2:
                raise MissingEvidence("CLI fresh/replay pair incomplete")
            fault_sets = [set(raw.get("observations", {})) for raw in replays]
            if (fault_sets[0] != fault_sets[1] or
                    not {"healthy", "dev_full", "closed_reader", "closed_stdout"} <= fault_sets[0]):
                raise MissingEvidence("CLI fault inventory incomplete")
            for replay, raw in enumerate(replays):
                for fault, pair in raw["observations"].items():
                    values = {}
                    # Same scope rule as fuzzing_io.cli_gate: a case without declared output
                    # directories makes no read-only claim, even when another case earned the
                    # task's readonly_output credit and no task-wide applicability evidence exists.
                    readonly_out_of_scope = (
                        fault == "readonly_dir"
                        and (evidence.get("readonly_applicability_evidence") or not spec["output_dirs"])
                        and all(pair.get(arm, {}).get("status") == "unsupported" for arm in ARMS))
                    for arm in ARMS:
                        row = pair.get(arm, {})
                        if not re.fullmatch(r"[a-f0-9]{64}", row.get("observer_sha256", "")):
                            raise MissingEvidence("Hash-bound CLI observer report missing")
                        key = f"{case['case_sha256']}/{replay}/{fault}"
                        add(arm, key, row, row.get("execution_affinity"))
                        if readonly_out_of_scope:
                            continue  # Same shared CLI scope rule; still verify the observer.
                        if row.get("status") != "observed":
                            raise MissingEvidence("CLI observation incomplete")
                        values[arm] = cli_semantic(row, context["out"], policy, spec)
                    if values:
                        if set(values) != set(ARMS):
                            raise MissingEvidence("Partial CLI arm pair")
                        difference(key, values["baseline"], values["candidate"],
                                   equal_cli(values["baseline"], values["candidate"], policy))
    elif name == "header_consumers":
        replays = evidence.get("runs", [])
        if len(replays) != 2:
            raise MissingEvidence("Header fresh/replay pair incomplete")
        for replay, raw in enumerate(replays):
            for arm in ARMS:
                worker = raw.get(arm, {})
                for cell in worker.get("cells", []):
                    if cell.get("status") != "completed":
                        raise MissingEvidence("Header cell not completed")
                    add(arm, f"{cell['id']}/{replay}", cell, worker.get("execution_affinity"))
            for cell in raw.get("cells", []):
                if cell.get("attribution") == "candidate_compatibility_difference":
                    repros.append({"gate": name, "replay": replay, "cell": cell,
                                   "requested_cpus": cpus})
    else:
        raw = evidence.get("cpu_raw")
        if not isinstance(raw, dict):
            raise MissingEvidence("Library callback did not retain raw CPU observations")
        module = context.get("hooks")
        comparator = getattr(module, "COMPARE", contract.exact_contract_compare)
        precondition = getattr(module, "PRECONDITION", None)
        for row in raw.get("cases", []):
            case, surface = row["case"], row["surface"]
            if case["task"] != context["task"].name:
                raise InvalidEvidence("Library case belongs to a different task")
            if not contract.public_case(case, surface, precondition)["valid"]:
                continue
            for prefix in (("", "fresh_") if name == "repeat_in_process" else ("",)):
                left, right = (row.get(prefix + arm, []) for arm in ARMS)
                if len(left) != len(right) or len(left) < 2:
                    raise MissingEvidence("Library fresh/replay observations incomplete")
                for index, (a, b) in enumerate(zip(left, right)):
                    key = f"{contract.case_digest(case)}/{prefix}{index}"
                    for arm, value in zip(ARMS, (a, b)):
                        contract.validate_observation(value, case, context["builds"][arm],
                            surface.get("oracle", {}).get("required_error_fields"))
                        add(arm, key, value, value.get("diagnostics", {}).get("execution_affinity"))
                    difference(key, a, b, contract.compare(comparator, a, b, case)["equal"])
    ids = {arm: [run["run_id"] for run in runs if run["arm"] == arm] for arm in ARMS}
    if (not ids["baseline"] or ids["baseline"] != ids["candidate"] or
            len(set(ids["baseline"])) != len(ids["baseline"])):
        raise MissingEvidence("Missing, duplicate, or unpaired execution inventory")
    if len(ids["baseline"]) > context["cpu_limits"]["observations_per_arm"]:
        raise MissingEvidence("Observation bound exceeded; corpus was not sampled")
    return runs, repros


def check(task, baseline, candidate, image, out, *, public_inputs=None,
          projects=None, builds=None, sources=None, runners=None, limits=None):
    """Return correctness_receipt-compatible evidence for CPUs 0, 0-1, 0-3, 0-7.

    ``runners`` is a verifier/test-only callback registry, never candidate input.
    Normal execution combines shared RUNNERS and task_runners(task). No timing
    entry point, score, calibration or A/A sampling is invoked.
    """
    task, out = Path(task).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    bounds = {**DEFAULT_LIMITS, **(limits or {})}
    if set(bounds) != set(DEFAULT_LIMITS) or any(type(v) is not int or v <= 0 for v in bounds.values()):
        raise ValueError("CPU limits must be positive integer bounds")
    if not re.fullmatch(r"(?:sha256:|.+@sha256:)[a-f0-9]{64}", image):
        raise ValueError("CPU replay requires an immutable Docker image identity")
    context = {"task": task, "baseline": Path(baseline).resolve(),
               "candidate": Path(candidate).resolve(), "image": image,
               "public_inputs": public_inputs, "projects": projects, "sources": sources,
               "cpu_limits": bounds}
    callbacks, module = generic.task_runners(task) if runners is None else ({}, None)
    shared = generic.RUNNERS if runners is None else runners
    if set(callbacks) & set(shared):
        raise ValueError("Task hook cannot override a shared generic runner")
    callbacks = {**shared, **callbacks}
    if set(callbacks) - set(GATES):
        raise ValueError("Unknown CPU correctness runner")
    if builds is None:
        from .verifier import build_inventory
        builds = {arm: build_inventory(context[arm]) for arm in ARMS}
    if any(not re.fullmatch(r"[a-f0-9]{64}", builds.get(arm, "")) for arm in ARMS):
        raise ValueError("Admitted build inventory commitments required")
    context.update(builds=builds, hooks=module)
    receipt = {"schema": "judge-cpu-correctness-v1", "timing_performed": False,
               "aa_sampling_performed": False, "task": task.name, "image": image,
               "builds": builds, "sources": sources, "limits": bounds, "records": []}
    inventories = {}
    for count in cpu_policy.CORRECTNESS_CPU_COUNTS:
        cpus = list(range(count))
        subset_out = out / f"cpu-{count}"
        subset_out.mkdir()
        ctx = {**context, "cpuset": ",".join(map(str, cpus)), "cpu_requested": cpus,
               "out": subset_out, "gate_results": {}}
        row = {"cpu_count": count, "requested_cpus": cpus, "actual_cpus": [],
               "passed": False, "affinity_verified": False, "baseline_cases": 0,
               "candidate_cases": 0, "gates": {}}
        statuses, all_runs, repros = [], [], []
        try:
            with _deadline(bounds["seconds_per_subset"]):
                for name in GATES:
                    try:
                        result = callbacks[name](ctx) if name in callbacks else {
                            "status": "pending", "reason": "Protected runner not registered"}
                    except SubsetTimeout:
                        raise
                    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError) as error:
                        result = {"status": "pending", "reason": type(error).__name__ + ": " + str(error)}
                    binding = _save(subset_out / (name + ".json"), result)
                    ctx["gate_results"][name] = result
                    status = result.get("status")
                    row["gates"][name] = {"evidence": binding, "status": status}
                    if status == "not_applicable" and result.get("applicability_evidence"):
                        continue
                    try:
                        runs, differences = _normalize(name, result, ctx)
                    except InvalidEvidence as error:
                        statuses.append("invalid")
                        row["gates"][name]["reason"] = str(error)
                        continue
                    except MissingEvidence as error:
                        statuses.append("unmeasured")
                        row["gates"][name]["reason"] = str(error)
                        continue
                    all_runs.extend({**run, "gate": name} for run in runs)
                    repros.extend(differences)
                    if status == "contract_regression" and not differences:
                        repros.append({"gate": name, "requested_cpus": cpus,
                                       "existing_comparator_repro": binding})
                    inventory = [(run["arm"], run["run_id"]) for run in runs]
                    if name in inventories and inventories[name] != inventory:
                        raise MissingEvidence("Protected case inventory changed between CPU subsets")
                    inventories[name] = inventory
                    statuses.append("contract_regression" if differences or status == "contract_regression"
                                    else "passed" if status == "passed" else "unmeasured")
        except InvalidEvidence as error:
            statuses.append("invalid")
            row["reason"] = str(error)
        except (MissingEvidence, SubsetTimeout, OSError, ValueError, TypeError, KeyError,
                RuntimeError, AttributeError) as error:
            statuses.append("unmeasured")
            row["reason"] = type(error).__name__ + ": " + str(error)
            if getattr(error, "__notes__", None):
                row["cleanup_notes"] = error.__notes__
        for arm in ARMS:
            row[arm + "_cases"] = sum(run["arm"] == arm for run in all_runs)
        if not all_runs or row["baseline_cases"] > bounds["observations_per_arm"]:
            statuses.append("unmeasured")
        # Physical mispinning overrides semantic blame; incomplete proof cannot pass.
        row["status"] = next((s for s in ("invalid", "contract_regression", "unmeasured")
                              if s in statuses), "passed")
        row["passed"] = row["status"] == "passed"
        row["affinity_verified"] = bool(all_runs) and row["status"] not in ("invalid", "unmeasured")
        row["actual_cpus"] = cpus if row["affinity_verified"] else []
        row["execution_evidence"] = _save(subset_out / "executions.json", all_runs)
        if repros:
            row["repro"] = _save(subset_out / "repro.json", {
                "task": task.name, "image": image, "builds": builds,
                "cpuset": ctx["cpuset"], "differences": repros,
                "replay": "Repeat check with the same admitted builds and protected callbacks"})
        receipt["records"].append(row)
    receipt["passed"] = cpu_policy.correctness_receipt(receipt)["passed"]
    receipt["status"] = next((s for s in ("invalid", "contract_regression", "unmeasured")
                              if any(r["status"] == s for r in receipt["records"])), "passed")
    receipt["evidence"] = _save(out / "cpu-correctness.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("task", "baseline", "candidate", "image", "out"):
        parser.add_argument("--" + name, required=True)
    for name in ("public-inputs", "projects"):
        parser.add_argument("--" + name, type=Path)
    for arm in ARMS:
        parser.add_argument("--" + arm + "-source-sha256")
    args = parser.parse_args()
    options = vars(args)
    sources = {arm: options.pop(arm + "_source_sha256") for arm in ARMS}
    if any(sources.values()) and not all(sources.values()):
        parser.error("Both source SHA-256 commitments are required for header consumers")
    result = check(**options, sources=sources if all(sources.values()) else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
