import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback
from tools import qualify_aa as shared


def run(args):
    task, build = args.task.resolve(), args.build.resolve()
    public = args.scope == "public"
    if not public:
        shared.heldout.check(task / "tests", require_available=True)
    inputs = task / ("environment/workload/inputs" if public else "tests/heldout")
    settings = json.loads((task / "tests/judge.json").read_text())
    assert not settings.get("startup_probe"), "Revisit the wrapper if a startup probe is introduced"
    module = shared.load_task(task)
    cases = {case.id: case for case in module.cases(inputs)}
    roles = shared.case_roles(settings, cases, public)
    canonical = shared.performance_units(settings, roles, public)
    if canonical is None:
        canonical = {case: [case] for case, role in roles.items() if role == "performance"}
    assert len(set(args.units)) == len(args.units), "Repeated unit"
    assert all(name in canonical for name in args.units), "Unknown performance unit"
    units = {name: canonical[name] for name in args.units}
    expected = {name: shared.native_reference(module, case, list(cases.values()), inputs)
                for name, case in cases.items()}
    image = shared.image_id(args.image)
    identity = shared.reference_identity(task, image, build, args.image_build, args.build_receipt)
    execution = shared.score_host.select(image, args.cpuset)
    assert execution["pinning_took_effect"], "Eight-CPU Docker pinning is required"
    cores = execution["requested_cores"]

    def measure(name, arm):
        with tempfile.TemporaryDirectory(prefix="sqlglot-noise-case-") as temporary, \
                shared.heldout.workload_view(task / "tests", task / "environment/workload",
                                            enabled=not public) as workload:
            scratch = Path(temporary)
            context = shared.Context(scratch, build, inputs, scratch, workload, args.seed, docker=True)
            executor = shared.DockerExecutor(
                image, args.cpuset, memory=settings["memory"], pids_limit=settings["pids_limit"],
                extra_docker_flags=shared.HARDENING, tmpfs="/tmp:rw,nosuid,nodev,size=2g")
            outputs, steps = shared.run_case(module, cases[name], context, executor)
            observed = {key: shared.fingerprint(raw, getattr(module, "RESULT_ENCODING", None))
                        for key, raw in outputs.items()}
            resources = shared.resources(steps)
            return {"correct": observed == expected[name], "ns": executor.case_time_ns,
                    "resources": {key: resources.get(key)
                                  for key in ("peak_memory_bytes", "cpu_time_usec")},
                    "steps": [{"exit_code": step.exit_code, "leftovers": step.leftovers,
                               "execution_protocol": step.telemetry.get("execution_protocol")}
                              for step in steps]}

    before = shared.telemetry.counters(cores)
    started = time.time()
    records, summaries, correctness, error = [], {}, {}, None
    try:
        for name in cases:
            result = measure(name, "correctness")
            correctness[name] = {"correct": result["correct"], "ns": result["ns"]}
            assert result["correct"], "Complete-output correctness failed"
        summaries = shared.sample_units(
            units, measure, records, args.minimum, args.maximum, args.warmups,
            args.minimum_seconds, args.spread_pp, args.seed)
    except Exception as exc:
        error = type(exc).__name__
        traceback.print_exc()
    quality = shared.telemetry.quality(
        before, shared.telemetry.counters(cores), cores, **settings["noise_thresholds"])
    return {
        "mode": "unofficial-selected-units-noise-only",
        "claimable": False, "not_whole_task_qualification": True,
        "scope": args.scope, "canonical_roles_unchanged": roles,
        "canonical_units": canonical, "sampled_units": units,
        "complete_correctness": correctness, "units": summaries, "records": records,
        "quality": quality, "error_class": error,
        "passed": shared.qualification_passed(units, summaries, quality, error, args.spread_pp),
        "minimum_unit_seconds": args.minimum_seconds,
        "elapsed_seconds": time.time() - started, "image": image,
        "execution_host": execution, "reference_artifact_identity": identity,
        "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


parser = argparse.ArgumentParser()
for name in ("task", "build", "image-build", "build-receipt", "output"):
    parser.add_argument("--" + name, type=Path, required=True)
for name in ("image", "cpuset"):
    parser.add_argument("--" + name, required=True)
parser.add_argument("--scope", choices=("public", "heldout"), required=True)
parser.add_argument("--units", nargs="+", default=["optimizer"])
for name, default in (("minimum", 5), ("maximum", 30), ("warmups", 1), ("seed", 1729)):
    parser.add_argument("--" + name, type=int, default=default)
parser.add_argument("--minimum-seconds", type=float, default=1)
parser.add_argument("--spread-pp", type=float, default=3)
args = parser.parse_args()
result = run(args)
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: result[key] for key in ("scope", "passed", "error_class", "elapsed_seconds")}))
raise SystemExit(0 if result["passed"] else 1)
