#!/usr/bin/env python3
"""Same-build A/A qualification with frozen distinct units; never a candidate score."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from judge import heldout, public_input_preflight, telemetry, score_host
from judge.verifier import (load_task, case_roles, performance_units, measure_unit,
                            image_id, build_inventory, resources, HARDENING)
from judge.statistics import stopping
from judge.scheduler import orders
from judge.process_state import native_reference
from judge.leftovers import resident_workers
from runner import Context, run_case, fingerprint, contained
from docker_executor import DockerExecutor


def sample_units(units, measure, records, minimum=5, maximum=30, warmups=1,
                 minimum_seconds=1, spread_pp=5, seed=1729, before_timed=None):
    """Keep every pair and every observation; stop only after the full A/A gate."""
    if minimum < 5 or maximum < minimum or warmups < 1 or minimum_seconds < 1 or not 0 < spread_pp <= 5:
        raise ValueError("Invalid A/A qualification policy")
    rng, summaries = random.Random(seed), {}
    for unit, members in units.items():
        pairs = []
        schedule = orders(maximum, rng)
        warmup_times = []
        for index in range(-warmups, maximum):
            if index == 0 and before_timed:
                before_timed(unit, warmup_times)
            values = {}
            lanes = rng.sample(["a", "b"], 2) if index < 0 else [
                {"baseline": "a", "candidate": "b"}[lane] for lane in schedule[index]]
            for arm in lanes:
                result = measure_unit(members, measure, arm)
                records.append({"unit": unit, "arm": arm, "round": index,
                                "phase": "warmup" if index < 0 else "timed", **result})
                if not result["correct"]:
                    raise ValueError("Performance component output mismatch")
                if result["ns"] < minimum_seconds * 1_000_000_000:
                    raise ValueError(f"Distinct-input unit below {minimum_seconds} second: {unit}; ns={result['ns']}")
                values[arm] = result["ns"]
            if index < 0:
                warmup_times.extend(values.values())
                print(json.dumps({"unit": unit, "phase": "warmup", "round": index,
                                  "arm_ns": values}), file=sys.stderr, flush=True)
                continue
            pairs.append((values["a"], values["b"]))
            state = stopping(pairs, minimum, maximum, spread_pp / 200, seed + index)
            if "ci95" not in state:
                continue
            spread = 100 * (state["ci95"][1] - state["ci95"][0])
            summaries[unit] = {**state, "ci95_spread_percentage_points": spread}
            print(json.dumps({"unit": unit, "phase": "timed", "pairs": len(pairs),
                              "arm_ns": values, "ci95": state["ci95"],
                              "ci95_spread_percentage_points": spread}),
                  file=sys.stderr, flush=True)
            if state["status"] == "resolved" and spread <= spread_pp and state["ci95"][0] <= 1 <= state["ci95"][1]:
                break
    return summaries


def qualification_passed(units, summaries, quality, error, spread_pp=5):
    return (not error and set(summaries) == set(units) and not quality["noisy"] and
            all(row["status"] == "resolved" and row["ci95_spread_percentage_points"] <= spread_pp and
                row["ci95"][0] <= 1 <= row["ci95"][1] for row in summaries.values()))


def startup_share(process_times, unit_times, processes_per_unit=1):
    if not process_times or not unit_times or any(n <= 0 for n in [*process_times, *unit_times]):
        raise ValueError("Startup share requires positive process and unit observations")
    if type(processes_per_unit) is not int or processes_per_unit < 1:
        raise ValueError("Startup share requires a positive process count")
    process, unit = statistics.median(process_times), statistics.median(unit_times)
    return {"median_process_wall_ns": process, "median_unit_wall_ns": unit,
            "processes_per_unit": processes_per_unit,
            "estimated_unit_startup_ns": process * processes_per_unit,
            "share_fraction": process * processes_per_unit / unit,
            "share_percent": 100 * process * processes_per_unit / unit,
            "process_observations": len(process_times), "unit_observations": len(unit_times)}


def reference_identity(task, image, build, image_build=None, build_receipt=None):
    """Disclose whether the timed judge.build reference matches the image artifact."""
    actual = build_inventory(build)
    if (image_build is None) != (build_receipt is None):
        raise ValueError("Image baseline and judge.build receipt must be supplied together")
    if image_build is None:
        return {"timed_build_sha256": actual, "origin": "unspecified",
                "image_vs_rebuilt_identity": "not checked"}
    receipt = json.loads(Path(build_receipt).read_text())
    if (receipt.get("task"), receipt.get("image"), receipt.get("build_sha256")) != (
            task.name, image, actual):
        raise ValueError("judge.build receipt task/image/artifact commitment mismatch")
    original = build_inventory(image_build)
    return {"origin": "judge.build output", "timed_build_sha256": actual,
            "image_build_sha256": original, "rebuilt_build_sha256": actual,
            "source_sha256": receipt["source_sha256"],
            "build_receipt_sha256": heldout.sha(Path(build_receipt)),
            "image_vs_rebuilt_identity": "identical" if original == actual else "different",
            "both_aa_arms": "same judge.build output",
            "image_artifact_is_timed": False}


def run(task, build, image, cpuset, scope, projects=None, minimum=5, maximum=30, warmups=1,
        minimum_seconds=1, spread_pp=5, seed=1729, image_build=None, build_receipt=None):
    task, build = Path(task).resolve(), Path(build).resolve()
    public = scope == "public"
    if scope not in ("public", "heldout"):
        raise ValueError("Expected public or heldout scope")
    if not public:
        heldout.check(task / "tests", require_available=True)
    inputs = task / ("environment/workload/inputs" if public else "tests/heldout")
    settings = json.loads((task / "tests/judge.json").read_text())
    module = load_task(task)
    cases = {case.id: case for case in module.cases(inputs)}
    roles = case_roles(settings, cases, public)
    units = performance_units(settings, roles, public)
    if units is None:
        units = {case: [case] for case, role in roles.items() if role == "performance"}
    if not units:
        raise ValueError("No performance units")
    expected = {case: native_reference(module, row, list(cases.values()), inputs)
                for case, row in cases.items()}
    image = image_id(image)
    artifact_identity = reference_identity(task, image, build, image_build, build_receipt)
    execution = score_host.select(image, cpuset)
    if not execution["pinning_took_effect"]:
        raise ValueError("A/A qualification requires verified eight-CPU Docker pinning")
    cores = execution["requested_cores"]
    host = telemetry.fingerprint(cores, "qualification-unregistered")
    records, summaries, error, startup = [], {}, None, None

    def observe(case, selected_inputs, reference, private):
        with tempfile.TemporaryDirectory(prefix="qualification-aa-case-") as temporary, \
                heldout.workload_view(task / "tests", task / "environment/workload",
                                      enabled=private) as workload:
            scratch = Path(temporary)
            ctx = Context(scratch, build, selected_inputs, Path(projects) if projects else scratch,
                          workload, seed, docker=True)
            executor = DockerExecutor(image, cpuset, memory=settings["memory"],
                pids_limit=settings["pids_limit"], extra_docker_flags=HARDENING,
                tmpfs="/tmp:rw,nosuid,nodev,size=2g")
            outputs, steps = run_case(module, case, ctx, executor)
            observed = {name: fingerprint(raw, getattr(module, "RESULT_ENCODING", None))
                        for name, raw in outputs.items()}
            workers_finished = not resident_workers(steps)
            return {"correct": observed == reference and workers_finished,
                    "resident_workers_finished": workers_finished, "ns": executor.case_time_ns,
                    "resources": resources(steps), "outputs": observed,
                    "family": case.data.get("family", case.data.get("parent", case.id.split("--")[0])),
                    "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                               "execution_protocol": r.telemetry.get("execution_protocol")} for r in steps]}

    def measure(case, arm):
        return observe(cases[case], inputs, expected[case], not public)

    def before_timed(unit, unit_times):
        if startup is not None:
            ratio = startup_share(startup["process_wall_ns"], unit_times, len(units[unit]))
            startup["preflight_shares"][unit] = ratio
            if ratio["share_fraction"] > startup["maximum_share"]:
                raise ValueError("Startup share exceeds 2%; report for review before A/A timed pairs")

    start, before = time.time(), telemetry.counters(cores)
    try:
        probe = settings.get("startup_probe")
        if probe:
            if settings.get("resident_protocol") is not False or probe.get("scope") != "public":
                raise ValueError("Startup probe requires a non-resident target and explicit public scope")
            count, limit = probe.get("samples", 9), probe.get("maximum_share", .02)
            if type(count) is not int or not 5 <= count <= 31 or not 0 < limit <= .02:
                raise ValueError("Invalid startup-share probe policy")
            probe_inputs = task / "environment/workload/inputs"
            probe_cases = {case.id: case for case in module.cases(probe_inputs)}
            probe_id = probe["case"]
            if case_roles(settings, probe_cases, True).get(probe_id) != "correctness":
                raise ValueError("Startup probe must select an original correctness-only case")
            probe_case = probe_cases[probe_id]
            name = probe_case.data.get("input")
            if not isinstance(name, str) or contained(probe_inputs / "data", name).stat().st_size != 0:
                raise ValueError("Startup probe requires an authenticated empty input")
            reference = native_reference(module, probe_case, list(probe_cases.values()), probe_inputs)
            startup = {"scope": "public", "case": probe_id, "process_wall_ns": [],
                       "maximum_share": limit, "preflight_shares": {}, "final_shares": {},
                       "boundary": "native process startup included; container startup excluded"}
            for index in range(count):
                result = observe(probe_case, probe_inputs, reference, False)
                records.append({"case": probe_id, "phase": "startup", "index": index, **result})
                if (not result["correct"] or len(result["steps"]) != 1 or
                        result["steps"][0]["execution_protocol"] not in (None, "process") or
                        type(result["ns"]) is not int or result["ns"] <= 0):
                    raise ValueError("Startup probe failed its original output or process protocol")
                startup["process_wall_ns"].append(result["ns"])
        for case, role in roles.items():
            if role == "correctness":
                for arm in ("a", "b"):
                    result = measure(case, arm)
                    records.append({"case": case, "arm": arm, "phase": "correctness", **result})
                    if not result["correct"]:
                        raise ValueError("Correctness-only case mismatch: " + case)
        summaries = sample_units(units, measure, records, minimum, maximum, warmups,
                                 minimum_seconds, spread_pp, seed, before_timed)
    except Exception as exc:
        error = str(exc)
    if startup is not None and startup["process_wall_ns"]:
        for unit in units:
            times = [row["ns"] for row in records if row.get("unit") == unit]
            if times:
                ratio = startup_share(startup["process_wall_ns"], times, len(units[unit]))
                startup["final_shares"][unit] = ratio
                if ratio["share_fraction"] > startup["maximum_share"] and error is None:
                    error = "Startup share exceeds 2%; review required"
    quality = telemetry.quality(before, telemetry.counters(cores), cores, **settings["noise_thresholds"])
    return {"mode": "same-image-pristine-aa-qualification", "task": task.name, "scope": scope,
            "passed": qualification_passed(units, summaries, quality, error, spread_pp),
            "claimable": False, "candidate_verdict": None, "image": image,
            "build_sha256": build_inventory(build), "host": host, "execution_host": execution,
            "reference_artifact_identity": artifact_identity,
            "machine_identity": {"instance_type": None, "instance_type_status": "provider metadata unavailable",
                                 "cpu_model": host["model"], "kernel": host["kernel"],
                                 "online_cpus": host["online_cpu_count"],
                                 "cpu_max": telemetry.text("/sys/fs/cgroup/cpu.max"),
                                 "memory_max": telemetry.text("/sys/fs/cgroup/memory.max"),
                                 "environment_spec_sha256": heldout.sha(Path("/var/lib/replit/spec.yaml"))
                                 if Path("/var/lib/replit/spec.yaml").is_file() else None},
            "started_epoch": start, "elapsed_seconds": time.time() - start, "quality": quality,
            "performance_units": units, "roles": roles, "units": summaries, "records": records,
            "startup_probe": startup,
            "error": error, "minimum_unit_seconds": minimum_seconds,
            "maximum_ci95_spread_percentage_points": spread_pp, "seed": seed}


@contextmanager
def prepared_task(task, scope):
    """Prepare inputs before timing; fetch-only bodies never enter the checkout."""
    task = Path(task).resolve()
    if scope == "heldout":
        with heldout.materialized_task(task) as view:
            yield view
    else:
        if task.name == "tantivy-gharchive-single-segment-indexing":
            public_input_preflight.ensure_public_inputs(task, fetch=True)
            subprocess.run([sys.executable, str(task / "environment/prepare.py"),
                            str(task / "environment")], check=True)
        yield task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--scope", choices=["public", "heldout"], required=True)
    parser.add_argument("--projects", type=Path)
    parser.add_argument("--image-build", type=Path,
                        help="Extracted immutable image baseline; compared but not timed")
    parser.add_argument("--build-receipt", type=Path,
                        help="judge.build receipt bound to the selected timed build")
    parser.add_argument("--minimum", type=int, default=5)
    parser.add_argument("--maximum", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--minimum-seconds", type=float, default=1)
    parser.add_argument("--spread-pp", type=float, default=5)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("A/A output must be fresh")
    with prepared_task(args.task, args.scope) as task:
        result = run(task, args.build, args.image, args.cpuset, args.scope, args.projects,
                     args.minimum, args.maximum, args.warmups, args.minimum_seconds, args.spread_pp, args.seed,
                     args.image_build, args.build_receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("task", "scope", "passed", "error", "elapsed_seconds")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
