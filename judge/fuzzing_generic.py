"""Tonight's generic RC gates, separate from deep v1.1 hardening adequacy.

Runner registration is verifier-owned. An absent applicable runner is pending,
never an implicit pass. No sampling, calibration or performance score occurs.
"""
import hashlib
import json
import copy
import importlib.util
import sys
import types

from . import fuzzing_contract as contract
from . import fuzzing_lifecycle as lifecycle_policy
from pathlib import Path

PROFILE = "judge-v1.1-rc-generic"
GATES = ("cli_faults", "stream_faults", "repeat_in_process", "aliasing",
         "header_consumers", "process_state")
RUNNERS = {}


def _thread_snapshot(snapshot):
    values = snapshot.get("thread_identities")
    if values is None:
        return None
    if not isinstance(values, list) or len(values) != snapshot.get("threads"):
        raise ValueError("Thread count contradicts concrete thread identities")
    if not values:
        return None
    identities, tids = set(), set()
    for value in values:
        if (not isinstance(value, dict) or type(value.get("tid")) is not int or value["tid"] <= 0
                or type(value.get("start_ticks")) is not int or value["start_ticks"] < 0
                or not isinstance(value.get("state"), str) or len(value["state"]) != 1
                or value.get("state") in ("Z", "X", "x")):
            raise ValueError("Malformed or dead entry in live thread snapshot")
        if "flags" in value and (type(value["flags"]) is not int or value["flags"] & 4):
            raise ValueError("Kernel-exiting or malformed task reported as a live worker")
        if value["tid"] in tids:
            raise ValueError("Duplicate TID in live thread snapshot")
        tids.add(value["tid"])
        identities.add((value["tid"], value["start_ticks"]))
    if not any(value["tid"] == value.get("scope_pid", value.get("tgid")) for value in values):
        return None  # A live in-process worker must include its calling main task.
    return identities


def _call_evidence(name, raw, module, context):
    """Classify task-provided facts centrally; task hooks cannot award a verdict."""
    if not isinstance(raw, dict) or raw.get("format") != "judge-generic-call-evidence-v1":
        raise ValueError("Generic task hook returned malformed facts")
    if raw.get("applicable") is False:
        proof = raw.get("applicability_evidence")
        return {"status": "not_applicable" if proof else "pending",
                "applicability_evidence": proof, "reason": raw.get("reason")}
    rows = raw.get("cases", [])
    if raw.get("applicable") is not True or not isinstance(rows, list) or not rows:
        return {"status": "pending", "reason": "No applicable public cases supplied"}
    if context.get("cpu_limits") and len(rows) > context["cpu_limits"]["cases_per_gate"]:
        raise ValueError("CPU correctness case bound exceeded; refusing to sample")
    precondition = getattr(module, "PRECONDITION", None)
    if not callable(precondition):
        raise ValueError("RC task hook must supply PRECONDITION(case, surface)")
    comparator = getattr(module, "COMPARE", contract.exact_contract_compare)
    findings, observed, admitted_rows = [], [], []
    for row in rows:
        case, surface = row["case"], row["surface"]
        admission = contract.public_case(case, surface, precondition)
        if not admission["valid"]:
            observed.append({"admitted": False, "candidate_verdict": None,
                             "status": "out_of_contract", "reason": admission["reason"]})
            continue  # Explicitly quarantined; never candidate credit or a regression.
        left, right = row["baseline"], row["candidate"]
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right) or len(left) < 2:
            raise ValueError("Generic public cases need at least two corresponding fresh/replayed observations")
        result = contract.evaluate_case(case, surface, lambda *_: admission,
            lambda _case, arm, index: (left if arm == "baseline" else right)[index],
            context["builds"], compare_hook=comparator, repetitions=len(left))
        findings.extend(result["findings"])
        observed.append(result)
        admitted_rows.append(row)
    if not admitted_rows:
        return {"status": "pending", "reason": "No admitted public cases",
                "evidence": {"observations": observed}}
    if name == "repeat_in_process":
        workers = raw.get("workers", {})
        for arm in ("baseline", "candidate"):
            worker = workers.get(arm, {})
            if (worker.get("mode") != "persistent" or worker.get("target_library_in_process") is not True
                    or not isinstance(worker.get("worker_id"), str) or not worker["worker_id"]
                    or type(worker.get("calls")) is not int or worker["calls"] < 2):
                return {"status": "pending", "reason": "No actual same-process library-worker evidence"}
        # Every row must compare the persistent results to its fresh oracle.
        for row in admitted_rows:
            for arm in ("baseline", "candidate"):
                fresh = row.get("fresh_" + arm, [])
                if not fresh:
                    return {"status": "pending", "reason": "Fresh-process oracle missing"}
                for value in fresh:
                    contract.validate_observation(value, row["case"], context["builds"][arm],
                        row["surface"].get("oracle", {}).get("required_error_fields"))
                for value in row[arm]:
                    if not contract.compare(comparator, fresh[0], value, row["case"])["equal"]:
                        if arm == "baseline":
                            raise ValueError("Pristine fresh/repeated contractual behavior differs")
                        findings.append({"kind": "contract_regression", "reproduced": True})
        lifecycle = raw.get("lifecycle", [])
        if len(lifecycle) < 2:
            return {"status": "pending", "reason": "Per-call lifecycle evidence missing"}
        lifecycle_failures = {}
        ownership = lifecycle_policy.managed_policy(raw)
        for row in lifecycle:
            threads = {}
            for arm in ("baseline", "candidate"):
                data = row.get(arm, {})
                if data.get("available") is not True:
                    return {"status": "pending", "reason": "Per-call lifecycle counters unavailable"}
                for point in ("before", "after"):
                    if not all(type(data.get(point, {}).get(metric)) is int and data[point][metric] >= 0
                               for metric in ("fds", "threads", "children")):
                        raise ValueError("Malformed per-call lifecycle counter")
                    ids = _thread_snapshot(data[point])
                    if ids is None:
                        return {"status": "pending", "reason": "Concrete thread identities unavailable"}
                    threads[(arm, point)] = ids
            owned_differences = lifecycle_policy.differences(row, ownership)
            if owned_differences is None:
                return {"status": "pending", "reason": "Managed runtime ownership/profile is unmeasured",
                        "evidence": {"lifecycle": lifecycle, "lifecycle_policy": ownership}}
            native_flags_known = all(type(identity.get("flags")) is int
                                     for identity in row["candidate"]["after"]["thread_identities"]
                                     if identity["tid"] != identity.get("scope_pid", identity.get("tgid")))
            for metric in ("fds", "threads", "children"):
                if metric == "threads" and ownership["kind"] != "native-os-threads":
                    continue
                delta = {arm: row[arm]["after"][metric] - row[arm]["before"][metric]
                         for arm in ("baseline", "candidate")}
                if (delta["candidate"] > delta["baseline"] or
                        row["candidate"]["after"][metric] > row["baseline"]["after"][metric]):
                    if metric == "threads" and not native_flags_known:
                        findings.append({"kind": "undertested", "reproduced": False,
                                         "reason": "Native surviving-thread exit flags unavailable"})
                        continue
                    key = (row.get("case_id", "unspecified"), metric)
                    lifecycle_failures[key] = lifecycle_failures.get(key, 0) + 1
            new_threads = {arm: threads[(arm, "after")] - threads[(arm, "before")]
                           for arm in ("baseline", "candidate")}
            if (ownership["kind"] == "native-os-threads" and
                    len(new_threads["candidate"]) > len(new_threads["baseline"])):
                if not native_flags_known:
                    findings.append({"kind": "undertested", "reproduced": False,
                                     "reason": "Native surviving-thread exit flags unavailable"})
                else:
                    key = (row.get("case_id", "unspecified"), "live_thread_identity")
                    lifecycle_failures[key] = lifecycle_failures.get(key, 0) + 1
            for metric in owned_differences:
                key = (row.get("case_id", "unspecified"), metric)
                lifecycle_failures[key] = lifecycle_failures.get(key, 0) + 1
        for (case_id, metric), count in lifecycle_failures.items():
            findings.append({"kind": "needs_review" if count >= 2 else "undertested",
                             "reproduced": count >= 2, "reason": "Additional per-call " + metric,
                             "case_id": case_id, "observations": count})
    verdict = next((kind for kind in ("contract_regression", "nondeterministic", "needs_review", "undertested")
                    if any(f["kind"] == kind for f in findings)), None)
    return {"status": "pending" if verdict == "undertested" else verdict or "passed",
            "evidence": {"observations": observed, "findings": findings,
                         **({"cpu_raw": raw} if context.get("cpu_limits") else {}),
                         "workers": raw.get("workers"), "lifecycle": raw.get("lifecycle"),
                         "lifecycle_policy": raw.get("lifecycle_policy", {"kind": "native-os-threads"})}}


def _task_call(name, callback, module, context):
    raw = callback(context)
    result = _call_evidence(name, raw, module, context)
    if context.get("cpu_limits"):
        # Pending lifecycle qualification must not discard physical executions.
        result.setdefault("evidence", {})["cpu_raw"] = raw
    return result


def task_runners(task):
    """Private relative imports avoid cross-task host module-cache collisions."""
    tests = (Path(task) / "tests").resolve()
    supplied_root = tests / "fuzz"
    if supplied_root.is_symlink():
        raise ValueError("RC task hook directory must remain verifier-owned")
    root = supplied_root.resolve()
    if not root.is_relative_to(tests):
        raise ValueError("RC task hook directory escapes verifier ownership")
    path = root / "rc_hooks.py"
    if not path.is_file():
        return {}, None
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("RC task hooks must remain verifier-owned")
    name = "_optimbench_rc_" + hashlib.sha256(str(root).encode()).hexdigest()
    for cached in list(sys.modules):
        if cached == name or cached.startswith(name + "."):
            del sys.modules[cached]
    package = types.ModuleType(name)
    package.__path__ = [str(root)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".rc_hooks", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise ValueError("RC task hooks failed to import: " + type(error).__name__) from error
    callbacks = getattr(module, "GATES", {})
    if not isinstance(callbacks, dict) or any(gate not in {
            "stream_faults", "repeat_in_process", "aliasing"} or not callable(callback)
            for gate, callback in callbacks.items()):
        raise ValueError("Task GATES may provide only library/repeat/alias facts")
    return {gate: (lambda context, name=gate, callback=callback:
                   _task_call(name, callback, module, context))
            for gate, callback in callbacks.items()}, module


def summarize(records):
    records = copy.deepcopy(records)
    for name in GATES:
        if name not in records:
            records[name] = {"status": "pending", "reason": "Generic runner/applicability evidence not integrated"}
    for name, value in records.items():
        if name not in GATES or not isinstance(value, dict):
            raise ValueError("Malformed generic gate record")
        status = value.get("status")
        if status == "not_applicable" and not value.get("applicability_evidence"):
            value.update(status="invalid_run", reason="Not-applicable claim has no contract-scope evidence")
        elif status == "passed" and not value.get("evidence"):
            value.update(status="invalid_run", reason="Passing generic gate has no observations")
        elif status not in {"passed", "not_applicable", "pending", "invalid_run",
                            "contract_regression", "nondeterministic", "sanitizer_error", "needs_review",
                            "baseline_nonrepeatable"}:
            value.update(status="invalid_run", reason="Unknown generic gate status")
    priority = ("contract_regression", "sanitizer_error", "nondeterministic",
                "needs_review", "baseline_nonrepeatable", "invalid_run", "pending")
    found = next((status for status in priority if any(r["status"] == status for r in records.values())), None)
    status = {"pending": "generic_gates_pending", "invalid_run": "invalid run"}.get(found, found or "gates_passed")
    return {"profile": PROFILE, "status": status, "passed": found is None,
            "gates": records, "timing_performed": False, "aa_sampling_performed": False,
            "claimable": False, "deep_hardening": {
                "status": "pending", "admission_blocker_for_rc": False,
                "includes": ["coverage_guided", "changed_coverage", "mutation_minima", "sanitizer_campaigns"]}}


def check(task, baseline, candidate, image, cpuset, out, public_inputs=None,
          projects=None, process_state=None, runners=None, builds=None, sources=None):
    """Run registered generic gates; registrations must not inspect a candidate's model label."""
    task, out = Path(task), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    context = {"task": task, "baseline": Path(baseline), "candidate": Path(candidate),
               "image": image, "cpuset": cpuset, "out": out,
               "public_inputs": public_inputs, "projects": projects, "sources": sources}
    callbacks = RUNNERS if runners is None else runners
    if runners is None:
        callbacks = dict(callbacks)
        supplied, module = task_runners(task)
        if set(callbacks) & set(supplied):
            raise ValueError("Task hook cannot override a shared generic runner")
        callbacks.update(supplied)
        context["hooks"] = module
    if callbacks:
        if builds is None:
            from .verifier import build_inventory
            builds = {arm: build_inventory(context[arm]) for arm in ("baseline", "candidate")}
        context["builds"] = builds
    records = {}
    state = process_state or {}
    if state.get("passed") is True:
        records["process_state"] = {"status": "passed", "evidence": state}
    else:
        records["process_state"] = {"status": "needs_review", "evidence": state,
                                    "reason": "Post-call process-state differences are a hard block; no approval path"}
    context["gate_results"] = records
    for name in sorted(callbacks, key=lambda item: item in ("cli_faults", "header_consumers")):
        runner = callbacks[name]
        if name not in GATES or name == "process_state":
            raise ValueError("Invalid generic runner registration")
        try:
            records[name] = runner(context)
        except (ValueError, OSError, TypeError, KeyError, RuntimeError, AttributeError) as error:
            records[name] = {"status": "baseline_nonrepeatable" if type(error).__name__ == "BaselineNonrepeatable"
                                      else "invalid_run", "reason": type(error).__name__ + ": " + str(error),
                             "candidate_verdict": None, "evidence": getattr(error, "evidence", None)}
    result = summarize(records)
    result["task"] = task.name
    raw = json.dumps(result, sort_keys=True, indent=2) + "\n"
    (out / "generic-gates.json").write_text(raw)
    result["receipt_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    return result


from .fuzzing_io import cli_gate, header_gate
RUNNERS.update(cli_faults=cli_gate, header_consumers=header_gate)
