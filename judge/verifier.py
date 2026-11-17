"""Shared-runner verifier entry. Public plumbing is explicitly not a scored run."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Context, HarnessError, references, run_case, fingerprint, compare_results
from docker_executor import DockerExecutor

from . import calibration, heldout, policy, scheduler, telemetry, upstream
from .source_policy import digest
from .leftovers import resident_workers
from . import interfaces
from . import behavior
from . import runtime_state
from .process_state import CandidateOutputRejected, native_reference

# UID 10000 is established by the executor: SETUID/SETGID/CHOWN/FOWNER/
# DAC_OVERRIDE are no longer necessary. PID 1 and its children share the UID,
# so KILL also is unnecessary. Retain cap-drop ALL, no-new-privileges.
HARDENING = ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]


def image_id(image):
    return subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                   text=True).strip()


def load_task(task):
    spec = importlib.util.spec_from_file_location("judge_task", task / "environment/workload/task.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_inventory(root):
    """Bind upstream gates to exact build content, including link targets."""
    inventory = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            inventory[relative] = {"symlink": os.readlink(path)}
        elif path.is_file():
            inventory[relative] = heldout.sha(path)
    if not inventory:
        raise ValueError("Empty candidate build")
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def resources(steps):
    """Cgroup counters cover the case container, not getrusage descendants."""
    peaks = [int(r.telemetry["memory.peak"]) for r in steps
             if r.telemetry.get("memory.peak", "").strip().isdigit()]
    last = steps[-1].telemetry.get("cpu.stat", "") if steps else ""
    cpu = {name: int(value) for name, value in (line.split() for line in last.splitlines())}
    measured = [r.telemetry.get("operation_cpu_time_usec") for r in steps]
    operation_cpu = (sum(measured) if measured and all(
        type(value) is int and value >= 0 for value in measured) else None)
    return {"peak_memory_bytes": max(peaks) if peaks else None, "cpu_time_usec": operation_cpu,
            "container_lifetime_cpu_time_usec": cpu.get("usage_usec"),
            "cpu_stat": cpu, "source": "shim cgroup readings"}


def public_input_path(task, supplied):
    """Image-staged data may be external, but frozen reference metadata may not change."""
    inputs = Path(supplied).resolve()
    original = task / "environment/workload/inputs"
    if (inputs / "manifest.json").read_bytes() != (original / "manifest.json").read_bytes():
        raise HarnessError("Staged public manifest differs from the task's frozen manifest")
    descriptor = json.loads((original / "manifest.json").read_text())
    if descriptor.get("format") == "optimbench-locked-cases-v1":
        for field in ("cases", "lock", "references"):
            relative = Path(descriptor[field])
            if relative.is_absolute() or ".." in relative.parts:
                raise HarnessError("Unsafe public metadata descriptor")
            if (inputs.parent / relative).read_bytes() != (original.parent / relative).read_bytes():
                raise HarnessError("Staged public reference metadata differs: " + field)
    return inputs


def case_roles(settings, cases, public_only):
    """Verifier-only roles, separate from frozen input/output commitments."""
    roles = settings.get("case_roles", {}).get("public" if public_only else "heldout", {})
    if not isinstance(roles, dict) or set(roles) - set(cases):
        raise ValueError("Case roles must name cases in the selected public/heldout split")
    if any(role not in ("performance", "correctness") for role in roles.values()):
        raise ValueError("Case role must be performance or correctness")
    return {case: roles.get(case, "performance") for case in cases}


def performance_units(settings, roles, public_only):
    """Frozen distinct-case groups; no new inputs, repeats or agent tuning."""
    groups = settings.get("performance_units", {}).get("public" if public_only else "heldout")
    if groups is None:
        return None
    if not isinstance(groups, dict) or not groups:
        raise ValueError("Performance units must be a nonempty mapping")
    expected = {case for case, role in roles.items() if role == "performance"}
    seen = set()
    for unit, members in groups.items():
        if (not isinstance(unit, str) or not unit or unit in roles and roles[unit] == "correctness" or
                not isinstance(members, list) or not members or
                not all(isinstance(case, str) for case in members)):
            raise ValueError("Invalid performance unit")
        for case in members:
            if case not in expected or case in seen:
                raise ValueError("Performance units contain a duplicate, unknown or correctness case")
            seen.add(case)
    if seen != expected:
        raise ValueError("Performance units must cover every performance case exactly once")
    return groups


AA_BUILDS = ("baseline_a1", "baseline_a2")
TIMING_COPIES = {"baseline": "baseline", "candidate": "candidate",
                 AA_BUILDS[0]: "aa-1", AA_BUILDS[1]: "aa-2"}


DEFAULT_TIMING_COPIES = 4


def timing_copy_count(settings, requested=None):
    count = settings.get("timing_copies_per_build", DEFAULT_TIMING_COPIES) if requested is None else requested
    if type(count) is not int or not 1 <= count <= 16:
        raise ValueError("timing_copies_per_build must be an integer from 1 to 16")
    return count


def _inventory(root):
    """build_inventory(root), reading each inode once, plus per-file link keys.

    Returns the identical commitment and {normalized path: (sha256, mode, mtime_ns)}
    for regular files, so identical files can be hard-linked within one copy.
    """
    inventory, keys, by_inode = {}, {}, {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            inventory[relative] = {"symlink": os.readlink(path)}
        elif path.is_file():
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity not in by_inode:
                by_inode[identity] = heldout.sha(path)
            inventory[relative] = by_inode[identity]
            keys[os.path.normpath(str(path))] = (by_inode[identity], stat.st_mode, stat.st_mtime_ns)
    if not inventory:
        raise ValueError("Empty candidate build")
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(), keys


def _copy_linked(source, destination, keys):
    """copytree that hard-links files with identical bytes, mode and mtime within one copy.

    Builds often hold hard-linked duplicates (Git's builtins), which earlier
    copytree steps expanded; the content commitment is unchanged, the copy is
    read-only to the workload, and mtimes are kept so cached bytecode stays valid.
    """
    first, stats = {}, {"files": 0, "linked": 0, "bytes_written": 0}

    def copy(src, dst):
        key = keys[os.path.normpath(src)]
        stats["files"] += 1
        if key in first:
            try:
                os.link(first[key], dst)
                stats["linked"] += 1
                return dst
            except OSError:
                pass
        shutil.copy2(src, dst)
        first.setdefault(key, dst)
        stats["bytes_written"] += os.path.getsize(dst)
        return dst
    shutil.copytree(str(source), str(destination), symlinks=True, copy_function=copy)
    return stats


def timing_builds(baseline, candidate, root, seed, expected_baseline_sha256=None, copies=1):
    """Fresh, byte-identical timing copies made together on one filesystem.

    Identical bytes at different host files can time differently (page-cache
    placement is fixed per file for a session). Four disjoint lanes are timed:
    baseline and candidate (A/B) and two baseline-byte lanes (A/A), each with K
    copies at root/<lane>/<k>, written in one seeded random order, hard-linking
    identical files within each copy, then synced so writeback does not overlap
    timing. Every copy's inventory is verified against its source.
    """
    if type(copies) is not int or not 1 <= copies <= 16:
        raise HarnessError("Timing copies per build must be from 1 to 16")
    root = Path(root)
    if root.exists():
        raise HarnessError("Timing copy root must be fresh")
    root.mkdir(parents=True)
    baseline_sha, baseline_keys = _inventory(Path(baseline))
    candidate_sha, candidate_keys = _inventory(Path(candidate))
    if expected_baseline_sha256 is not None and baseline_sha != expected_baseline_sha256:
        raise HarnessError("Baseline differs from its admitted rebuilt reference before timing copy")
    sources = {"baseline": (Path(baseline), baseline_sha, baseline_keys),
               "candidate": (Path(candidate), candidate_sha, candidate_keys),
               AA_BUILDS[0]: (Path(baseline), baseline_sha, baseline_keys),
               AA_BUILDS[1]: (Path(baseline), baseline_sha, baseline_keys)}
    writes = [(name, index) for name in sorted(sources) for index in range(copies)]
    order = random.Random(seed).sample(writes, len(writes))
    copy_stats = {}
    for name, index in order:
        source, _, keys = sources[name]
        copy_stats[f"{name}:{index}"] = _copy_linked(source, root / TIMING_COPIES[name] / str(index), keys)
    os.sync()
    builds = {name: [root / TIMING_COPIES[name] / str(index) for index in range(copies)]
              for name in sources}
    for name, paths in builds.items():
        if any(_inventory(path)[0] != sources[name][1] for path in paths):
            raise HarnessError("Timing copy inventory differs from its source build")
    # The copy root is a host-local (possibly temporary) path; keep it out of results.
    receipt = {"version": "timing-copies-v3", "lanes": {"ab": ["baseline", "candidate"], "aa": list(AA_BUILDS)},
               "build_sha256": {name: value[1] for name, value in sources.items()},
               "copies_per_build": copies, "synced_before_timing": True,
               "write_order": [f"{name}:{index}" for name, index in order],
               "bytes_written": sum(row["bytes_written"] for row in copy_stats.values()),
               "files_hard_linked": sum(row["linked"] for row in copy_stats.values()),
               "rule": "K fresh copies of each of four disjoint lanes (baseline, candidate and two "
                       "baseline-byte A/A lanes), made in one step in seeded random order, identical "
                       "files hard-linked within a copy, synced, every inventory verified"}
    return builds, receipt, root


def measure_unit(members, measure, build, copy=None):
    """Independent containers per original case; aggregate only existing intervals."""
    # Every member of one unit sample runs from the same rotated copy.
    components = [{"case": case, **(measure(case, build) if copy is None else measure(case, build, copy))}
                  for case in members]
    if any(type(row.get("ns")) is not int or row["ns"] <= 0 for row in components):
        raise ValueError("Invalid component shim time")
    peaks = [row.get("resources", {}).get("peak_memory_bytes") for row in components]
    peaks = [peak for peak in peaks if peak is not None]
    cpu = [row.get("resources", {}).get("cpu_time_usec") for row in components]
    return {"correct": all(row["correct"] for row in components),
            "ns": sum(row["ns"] for row in components), "components": components,
            "resources": {"peak_memory_bytes": max(peaks) if peaks else None,
                          "cpu_time_usec": sum(cpu) if all(n is not None for n in cpu) else None,
                          "source": "independent component shim cgroup readings"},
            "steps": [{"component": row["case"], **step}
                      for row in components for step in row.get("steps", [])],
            "outputs": {row["case"]: row.get("outputs", {}) for row in components}}


@heldout.acquired
def evaluate(task, baseline, candidate, projects, image, cpuset, cpu_class, plumbing=False,
             seed=None, minimum=None, maximum=None, half_width=None, warmups=None, calibration_path=None,
             upstream_receipt=None, source_sha256=None, noise=None, dry_run=False,
             provisional=False, execution_host=None, public_inputs=None, review_findings=None,
             gate_output=None, behavior_receipt=None, reference_info=None, registration_context=None,
             timing_root=None, timing_copies=None, score_through=False):
    task, baseline, candidate, projects = map(lambda p: Path(p).resolve(),
                                               (task, baseline, candidate, projects))
    registered = registration_context is not None
    if registered:
        from .host_registry import RegistrationContext, prepare_registered_session, ROOT as REGISTRY_ROOT
        if provisional or not isinstance(registration_context, RegistrationContext):
            raise HarnessError("Registered measurement requires a prepared context, not provisional relabeling")
        fresh = prepare_registered_session(
            task.name, REGISTRY_ROOT / "judge/hosts/registry.json",
            mandatory_gates=registration_context.mandatory_gates,
            gates=dict(registration_context.gates), session_control=registration_context.session_control)
        if fresh.as_dict() != registration_context.as_dict() or cpu_class != fresh.cpu_class:
            raise HarnessError("Registered session observations changed before timing")
        if (not execution_host or list(execution_host["effective_cores"]) !=
                list(fresh.live_fingerprint["judge_cpus"]) or not execution_host["pinning_took_effect"]):
            raise HarnessError("Registered measurement affinity does not match live registered judge CPUs")
    docker_observed = provisional or registered
    if dry_run and not plumbing:
        raise ValueError("Dry-run mode is only available for explicit public plumbing checks")
    if seed is None:
        seed = secrets.randbits(63)
    if docker_observed:
        if not execution_host:
            raise HarnessError("Provisional execution requires a verified Docker host probe")
        cores = execution_host["effective_cores"]
        host = {**execution_host["host"], "cpu_class": cpu_class,
                "pinning_took_effect": execution_host["pinning_took_effect"],
                "requested_cores": execution_host["requested_cores"],
                "available_cores": execution_host["available_cores"]}
    else:
        cores = telemetry.pinned(cpuset)
        host = telemetry.fingerprint(cores, cpu_class)
    image = image_id(image)
    settings = json.loads((task / "tests/judge.json").read_text())
    from .sampling_policy import resolve
    sampling = resolve(settings, minimum, maximum, warmups, half_width)
    minimum, maximum = (sampling["effective"]["minimum_pairs"],
                        sampling["effective"]["maximum_pairs"])
    warmups, half_width = (sampling["effective"]["warmups"],
                          sampling["effective"]["relative_ci_half_width"])
    aa_bar = settings.get("aa_max_full_width_pp", 5.0)
    if settings.get("public_dry_run_only") and (not plumbing or not dry_run):
        raise ValueError(settings["scoring_blocker"])
    lock = heldout.check(task / "tests", require_available=not plumbing and not provisional,
                         allow_missing_references=plumbing)
    public_only = plumbing or (provisional and lock["status"] != "available")
    if registered and registration_context.scope != ("public" if public_only else "heldout"):
        raise HarnessError("Registered qualification scope does not match this session")
    if not public_only:
        heldout.check(task / "tests", require_available=True)
    if not plumbing:
        from . import reference
        reference_info = reference.validate_call(task, image, baseline, reference_info)
    try:
        interface = interfaces.check(task, baseline, candidate, image, cpuset)
    except interfaces.InterfaceBroken as error:
        if score_through:
            interface = error.report
        else:
            return {"status": "interface_broken", "correct": False, "claimable": False,
                    "speedup": None, "public_interface": error.report, "task": task.name,
                    "image": image, "source_sha256": source_sha256,
                    "baseline_build_sha256": build_inventory(baseline),
                    "candidate_build_sha256": build_inventory(candidate)}
    inputs = task / ("environment/workload/inputs" if public_only else "tests/heldout")
    if not public_only:
        projects = heldout.project_root(task / "tests", projects)
    if public_inputs is not None:
        if not public_only:
            raise ValueError("Public input staging cannot replace private scored inputs")
        inputs = public_input_path(task, public_inputs)
    behavioral = behavior_receipt or behavior.check(
        task, baseline, candidate, image, cpuset, gate_output, inputs, projects)
    runtime_review = runtime_state.review(task, review_findings, behavioral)
    if not score_through and (behavioral.get("status") in ("differential_mismatch", "needs_review")
                              or runtime_review["runtime_configuration_review"]):
        status = behavioral.get("status")
        if status not in ("differential_mismatch", "needs_review"):
            status = "needs_review"
        return {**behavioral, "status": status, "correct": False if status == "differential_mismatch" else None,
                "claimable": False, "speedup": None, **runtime_review,
                "public_interface": interface, "task": task.name, "image": image,
                "source_sha256": source_sha256, "baseline_build_sha256": build_inventory(baseline),
                "candidate_build_sha256": build_inventory(candidate)}
    if not plumbing:
        if not source_sha256 or not upstream_receipt:
            raise ValueError("Source-policy admission and upstream receipt required")
        receipt = upstream.validate_receipt(Path(upstream_receipt), task.name, image,
                                            source_sha256, build_inventory(candidate),
                                            allow_rejected=score_through)
        if (receipt.get("reference_key") != reference_info["key"]
                or receipt.get("reference_build_sha256") != reference_info["build_sha256"]):
            raise ValueError("Upstream receipt is not bound to the admitted rebuilt reference")
        if not provisional and not registered:
            calibration.require(json.loads(Path(calibration_path).read_text()), host, image,
                                baseline_build_sha256=reference_info["build_sha256"],
                                reference_key=reference_info["key"])
    module = load_task(task)
    cases = {case.id: case for case in module.cases(inputs)}
    if not cases:
        raise HarnessError("Empty case set")
    expected = {name: native_reference(module, row, list(cases.values()), inputs)
                for name, row in cases.items()}
    roles = case_roles(settings, cases, public_only)
    units = performance_units(settings, roles, public_only)
    owned_timing_root = None
    if timing_root is None:
        # Same filesystem as the candidate export; removed after the session.
        owned_timing_root = tempfile.TemporaryDirectory(prefix="judge-timing-", dir=candidate.parent)
        timing_root = Path(owned_timing_root.name) / "timing"
    copies = timing_copy_count(settings, timing_copies)
    builds, timing_copies, timing_copy_root = timing_builds(
        baseline, candidate, Path(timing_root).resolve(), seed,
        reference_info["build_sha256"] if reference_info else None, copies)
    reference_outputs = {}

    def measure(case, build, copy=0):
        with tempfile.TemporaryDirectory(prefix="judge-case-") as temporary, \
                heldout.workload_view(task / "tests", task / "environment/workload",
                                      enabled=not public_only) as workload:
            ctx = Context(Path(temporary), builds[build][copy], inputs, projects,
                          workload, seed, docker=True)
            executor = DockerExecutor(image, cpuset=cpuset, memory=settings["memory"],
                                      pids_limit=settings["pids_limit"],
                                      extra_docker_flags=HARDENING,
                                      tmpfs="/tmp:rw,nosuid,nodev,size=2g", task=task)
            outputs, steps = run_case(module, cases[case], ctx, executor)
            workers = resident_workers(steps)
            if workers and build != "candidate":
                raise HarnessError(f"Reference build has unfinished resident workers on case {case} ({build}): {workers}")
            observed = {name: fingerprint(data, getattr(module, "RESULT_ENCODING", None))
                        for name, data in outputs.items()}
            exact = observed == expected[case]
            if hasattr(module, "compare"):
                if case not in reference_outputs:
                    if build != "baseline":
                        # Bind a numeric oracle to an authenticated pristine result, never
                        # to the candidate or an unverified new baseline.
                        measure(case, "baseline")
                    elif exact:
                        reference_outputs[case] = outputs
                correct = case in reference_outputs and compare_results(module, reference_outputs[case], outputs)
            else:
                correct = exact
            correct = correct and not workers
            if not correct and build != "candidate":
                raise HarnessError(f"Reference build output mismatch on case {case} ({build})")
            usage = resources(steps)
            if usage["cpu_time_usec"] is None or usage["cpu_time_usec"] <= 0:
                raise HarnessError("Missing or nonpositive operation cgroup CPU accounting")
            return {"correct": correct,
                    "resident_workers_finished": not workers,
                    "ns": executor.case_time_ns,
                    "resources": usage,
                    "outputs": {name: {"bytes": size, "sha256": sha}
                                for name, (size, sha) in observed.items()},
                    "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                               "duration_ns": r.t1_ns - r.t0_ns,
                               "telemetry": r.telemetry} for r in steps]}

    if docker_observed:
        from .score_host import probe
        before_probe = probe(image, cpuset)
        before = before_probe["counters"]
    else:
        before = telemetry.counters(cores)
    try:
        if dry_run:
            records = []
            for case in cases:
                for build in ("baseline", "candidate"):
                    item = measure(case, build)
                    records.append({"case": case, "build": build, "phase": "plumbing", **item})
                    if not item["correct"]:
                        raise scheduler.IncorrectCandidate(records)
            result = {"correct": True, "status": "plumbing complete", "records": records,
                      "aa_passed": False, "statistics": None, "claimable": False}
        else:
            if units is None:
                result = scheduler.session(list(cases), measure, seed, warmups, minimum, maximum, half_width,
                                           roles=roles, copies=copies, aa_builds=AA_BUILDS,
                                           aa_max_full_width_pp=aa_bar, score_through=score_through)
            else:
                correctness = [case for case, role in roles.items() if role == "correctness"]
                execution_cases = [*units, *correctness]
                execution_roles = {**{unit: "performance" for unit in units},
                                   **{case: "correctness" for case in correctness}}
                def grouped_measure(unit, build, copy=0):  # scheduler passes copy under rotation
                    return (measure_unit(units[unit], measure, build, copy) if unit in units
                            else measure(unit, build, copy))
                result = scheduler.session(execution_cases, grouped_measure, seed, warmups, minimum,
                                           maximum, half_width, roles=execution_roles, copies=copies,
                                           aa_builds=AA_BUILDS, aa_max_full_width_pp=aa_bar, score_through=score_through)
                result.update(performance_units=units, original_case_roles=roles)
    except scheduler.IncorrectCandidate as error:
        result = {"correct": False, "status": "incorrect", "records": error.records,
                  "speedup": 1.0, "claimable": False, "aa_passed": False}
    finally:
        # Copies are verified byte-identical to the recorded inventories; keep no extra disk.
        shutil.rmtree(timing_copy_root, ignore_errors=True)
        if owned_timing_root is not None:
            owned_timing_root.cleanup()
    if docker_observed:
        after_probe = probe(image, cpuset)
        if before_probe["host"]["boot_id_sha256"] != after_probe["host"]["boot_id_sha256"]:
            raise HarnessError("Docker execution host changed during the measurement session")
        after = after_probe["counters"]
    else:
        after = telemetry.counters(cores)
    noise = noise if noise is not None else settings["noise_thresholds"]
    result.update(cpu_class=cpu_class, host=host, image=image, seed=seed,
                  sampling_policy=sampling,
                  quality=telemetry.quality(before, after, cores, **(noise or {})),
                  scope=("provisional public-only score" if public_only else "provisional held-out score")
                        if provisional else "public-case plumbing check only" if plumbing else "held-out verifier",
                  heldout_status=lock["status"], upstream_gate="skipped: plumbing" if plumbing else
                  "rejected (scored through)" if receipt.get("rejected") else "passed",
                  baseline_build_sha256=build_inventory(baseline),
                  candidate_build_sha256=build_inventory(candidate),
                  claimable=False, protocol="eight-core-rmit-williams-four-lane-fwer-v5", public_interface=interface,
                  timing_copies=timing_copies,
                  behavioral_gates=behavioral)
    if "fixed_workload_threads" in settings:
        result["workload_settings"] = {
            "threads": settings["fixed_workload_threads"], "simd": settings.get("simd"),
            "resident_protocol": settings.get("resident_protocol", False)}
    if provisional:
        result.update(provisional=True, public_only=public_only,
                      execution_host=execution_host,
                      calibration="in-session A/A only; not registered-machine calibration")
    elif registered:
        result.update(provisional=False, public_only=public_only, mode="registered",
                      registration=registration_context.as_dict(), execution_host=execution_host,
                      calibration="reviewed registry qualification plus current in-session A/A",
                      claimable=False)
    result.update(runtime_review)
    if reference_info is not None:
        result.update(reference_key=reference_info["key"],
                      reference_build_sha256=reference_info["build_sha256"])
    return result


def main():
    from . import public_input_preflight, reference, score
    if "--heldout-check" in sys.argv[1:]:
        from .heldout_check import main as check_main
        return check_main([arg for arg in sys.argv[1:] if arg != "--heldout-check"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--baseline", type=Path,
                        help="Optional expected rebuilt-reference path; image baselines are rejected")
    parser.add_argument("--reference-cache", type=Path, default=Path("/tmp/optimbench-score-cache"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--public-inputs", type=Path, help="Trusted image-staged inputs; frozen metadata is rechecked")
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--cpu-class", required=True)
    parser.add_argument("--plumbing", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="One correctness pass per build; plumbing only, no statistics")
    parser.add_argument("--seed", type=int, help="Replay seed; default uses OS randomness and records the seed")
    parser.add_argument("--minimum", type=int, default=None,
                        help="Explicit override; default: task minimum_pairs")
    parser.add_argument("--maximum", type=int, default=None,
                        help="Explicit override; default: task maximum_pairs")
    parser.add_argument("--half-width", type=float, default=None)
    parser.add_argument("--warmups", type=int, default=None)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--upstream-receipt", type=Path)
    parser.add_argument("--pristine", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--artifacts", type=Path, help="JSON: editable root -> exported directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    admission = None
    try:
        if not args.plumbing:
            source_policy = json.loads((args.task / "release.json").read_text())["source_policy"]
            if not args.pristine or (not args.source and not args.artifacts):
                raise ValueError("Harbor artifacts or complete candidate source and pristine tree required")
            if args.artifacts:
                with tempfile.TemporaryDirectory() as temporary:
                    admission = policy.artifacts_to_envelope(
                        score.artifacts(args.task, args.artifacts)[0], args.pristine,
                        Path(temporary) / "source", source_policy)
            else:
                admission = policy.envelope(args.source, args.pristine, source_policy)
        rebuilt_reference = None
        if args.plumbing:
            if args.baseline is None:
                raise ValueError("Public plumbing requires an explicit baseline path; it is not scoring")
        else:
            image = image_id(args.image)
            trusted = score.assets(args.task.resolve(), image, args.reference_cache.resolve())
            public_input_preflight.ensure_public_inputs(args.task, source=trusted / "public-workload")
            rebuilt_reference = reference.ensure(args.task, trusted, image, args.cpuset,
                                                 args.reference_cache)
            if args.baseline is not None and args.baseline.resolve() != rebuilt_reference["build"].resolve():
                raise reference.QualificationError("Use the admitted rebuilt reference, not an image baseline")
            args.baseline = rebuilt_reference["build"]
        result = evaluate(args.task, args.baseline, args.candidate, args.projects, args.image,
                          args.cpuset, args.cpu_class, args.plumbing, args.seed, args.minimum,
                          args.maximum, args.half_width, args.warmups, args.calibration,
                          args.upstream_receipt, policy.source_digest(admission) if admission else None,
                          dry_run=args.dry_run, public_inputs=args.public_inputs,
                          review_findings=admission.get("runtime_configuration_review") if admission else None,
                          gate_output=args.output.parent / (args.output.stem + "-behavior"),
                          reference_info=rebuilt_reference)
    except reference.QualificationError as error:
        result = {"status": "invalid run", "task_qualification": "failed",
                  "correct": None, "harness_error": str(error), "claimable": False}
    except CandidateOutputRejected as error:
        receipt = args.output.parent / (args.output.stem + "-native-output-rejection.json")
        score.save(receipt, error.report)
        result = {"status": "incorrect", "correct": False, "claimable": False,
                  "speedup": None, "native_output_rejection": error.report,
                  "native_output_rejection_receipt": str(receipt), "reasons": [str(error)],
                  "timing_performed": False, "aa_sampling_performed": False}
    except (HarnessError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        result = {"status": "invalid run", "harness_error": str(error), "claimable": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result.get("correct") else (1 if result["status"] in ("incorrect", "interface_broken", "differential_mismatch") else 2)


if __name__ == "__main__":
    raise SystemExit(main())
