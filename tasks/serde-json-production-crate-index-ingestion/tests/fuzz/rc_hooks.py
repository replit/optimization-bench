"""Verifier-owned public library hooks; the central controller assigns verdicts."""
import json
from pathlib import Path
import tempfile

from .adapter import precondition as PRECONDITION, compare as COMPARE


def _facts(context, filename):
    from judge.verifier import HARDENING, ROOT
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "serde-public-library"
    output.mkdir(parents=True, exist_ok=True)
    # Share actual native observations between the registered gates.
    cache_key = (str(output.resolve()), context["builds"]["baseline"],
                 context["builds"]["candidate"], context["cpuset"])
    cache = context.setdefault("_serde_rc_facts", {})
    if cache.get("key") != cache_key:
        with tempfile.TemporaryDirectory(prefix="serde-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]),
                          task/"environment/workload/inputs", scratch,
                          task/"environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[
                    *HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task/'tests/fuzz'}:/verifier-fuzz:ro",
                    "-v", f"{ROOT/'judge'}:/verifier-lib/judge:ro",
                ], tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            argv = ["python3", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"],
                    "--out", "/scratch/facts"]
            results = executor.run([Step(argv, timeout=300)], ctx, {"PYTHONPATH":"/verifier-lib"})
            for index, result in enumerate(results):
                (output/f"step-{index}.stdout").write_bytes(result.stdout)
                (output/f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 for result in results):
                raise ValueError("Serde RC library probe infrastructure failure; inspect retained streams")
            import shutil
            for name in ("stream-facts.json", "repeat-facts.json", "alias-facts.json"):
                shutil.copy2(scratch/"facts"/name, output/name)
            (output/"execution.json").write_text(json.dumps({
                "image":context["image"], "cpuset":context["cpuset"],
                "builds":context["builds"],
                "steps":[{"exit_code":r.exit_code, "leftovers":r.leftovers,
                          "telemetry":r.telemetry} for r in results]},
                indent=2, sort_keys=True)+"\n")
        cache["key"] = cache_key
    return json.loads((output/filename).read_text())


def stream_faults(context):
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


GATES = {"stream_faults":stream_faults, "repeat_in_process":repeat_in_process, "aliasing":aliasing}


def CLI_CASES(context):
    """A protected benchmark driver is not a public serde_json CLI.

    Only exclude its CLI I/O once the library I/O and repeat/lifecycle
    callbacks are registered AND observed.
    Missing coverage returns no applicability evidence, hence central pending
    (unmeasured), never an unconditional N/A.
    """
    from judge import fuzzing_contract as contract

    def unmeasured(reason):
        return {
            "format":"judge-generic-cli-cases-v1","applicable":False,
            "applicability_evidence":{},"coverage_status":"unmeasured","reason":reason,
        }
    required = ("stream_faults","repeat_in_process")
    if not all(callable(GATES.get(name)) for name in required):
        return unmeasured("Covering library callbacks are not registered")
    stream = _facts(context,"stream-facts.json")
    repeat = _facts(context,"repeat-facts.json")
    from .adapter import SURFACES
    for facts in (stream,repeat):
        if facts.get("applicable") is not True:
            return unmeasured("Covering library callback is unavailable")
        seen = set()
        for row in facts.get("cases",[]):
            case, surface = row["case"],row["surface"]
            if not contract.public_case(case,surface,PRECONDITION)["valid"]:
                continue
            for arm in ("baseline","candidate"):
                if len(row.get(arm,[])) < 2:
                    return unmeasured("Covering callback has incomplete paired observations")
                for observation in row[arm]:
                    contract.validate_observation(observation,case,context["builds"][arm],
                        surface.get("oracle",{}).get("required_error_fields"))
                    affinity = observation.get("diagnostics",{}).get("execution_affinity")
                    if not isinstance(affinity,list) or not affinity:
                        return unmeasured("Covering library observation has no measured affinity")
            seen.add(case["surface_id"])
        if not set(SURFACES) <= seen:
            return unmeasured("Covering writer/reader/stream surface is unmeasured")
    for arm in ("baseline","candidate"):
        worker = repeat.get("workers",{}).get(arm,{})
        if (worker.get("mode") != "persistent" or
                worker.get("target_library_in_process") is not True or worker.get("calls",0) < 2):
            return unmeasured("Covering repeat worker has no native in-process calls")
    if not repeat.get("lifecycle") or any(
            row.get(arm,{}).get("available") is not True
            for row in repeat["lifecycle"] for arm in ("baseline","candidate")):
        return unmeasured("Covering per-call lifecycle is unmeasured")
    return {
        "format":"judge-generic-cli-cases-v1","applicable":False,
        "reason":"Protected registry program is a benchmark harness, not a public serde_json CLI",
        "applicability_evidence":{
            "classification":"protected-benchmark-driver-not-public-library-CLI",
            "reasoning":"Only production src/**/*.rs library code is editable; protected driver is outside the public serde_json API",
            "contract_sources":["instruction.md:Exact edit policy",
                "environment/workload/driver/src/main.rs","environment/workload/driver/Cargo.toml"],
            "covering_callbacks":list(required),"covering_surface_ids":list(SURFACES),
            "coverage_condition":"Both registered callbacks supplied bound paired observations for all named surfaces, measured affinity, persistent calls and available per-call lifecycle",
            "facts_sha256":{"stream_faults":contract.digest(stream),
                            "repeat_in_process":contract.digest(repeat)},
            "builds":context["builds"],
            "healthy_output_comparator":"Frozen workload declared structured-JSON comparison; protected driver faults are not public CLI obligations",
        },
    }


def HEADER_CONSUMERS(context):
    return {
        "format":"judge-generic-header-request-v1","applicable":False,
        "reason":"Pinned serde_json exposes Rust APIs, not public C/C++ consumer headers",
        "applicability_evidence":{
            "upstream_commit":"20572a6d066958542d79cdd6cf1a7cd9f0c8e1f8",
            "source_archive_sha256":"d638770aea490d6b9b94b67fa14c3533778431a8715792b2fac80e7ca405d721",
            "scan":"Pinned pristine source excluding .git: zero .h/.hh/.hpp/.hxx files",
            "cargo":"Cargo.toml has Rust std/alloc/serde feature families and no exported C ABI header target",
            "source_contract":["environment/source.lock.json","environment/pristine-files.lock.json"],
        },
    }
