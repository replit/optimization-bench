"""Protected registrations; callbacks return raw facts, never task-side verdicts."""
import json
from pathlib import Path
import shutil
import tempfile

from .adapter import precondition as PRECONDITION


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "goldmark-public-library"
    output.mkdir(parents=True, exist_ok=True)
    cache = context.setdefault("_goldmark_rc_facts", {})
    key = (str(output.resolve()), context["image"], context["cpuset"],
           context["builds"]["baseline"], context["builds"]["candidate"])
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="goldmark-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]),
                          task/"environment/workload/inputs", scratch,
                          task/"environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[
                    *HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task/'tests/fuzz'}:/verifier-fuzz:ro",
                ], tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            args = ["python3", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"],
                    "--out", "/scratch/facts"]
            results = executor.run([Step(args, timeout=600)], ctx, {})
            for index, result in enumerate(results):
                (output/f"step-{index}.stdout").write_bytes(result.stdout)
                (output/f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 for result in results):
                raise ValueError("Goldmark RC probe infrastructure failure; retained worker streams")
            for name in ("stream-facts.json", "repeat-facts.json", "alias-facts.json", "build-receipt.json"):
                shutil.copy2(scratch/"facts"/name, output/name)
            (output/"execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"],
                "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results]}, indent=2)+"\n")
        cache["key"] = key
    return json.loads((output/filename).read_text())


def stream_faults(context):
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


def HEADER_CONSUMERS(context):
    scope = json.loads((Path(__file__).parent/"header-scope.json").read_text())
    return {"format": "judge-generic-header-request-v1", "applicable": False,
            "reason": scope["header_consumer_matrix"]["reason"],
            "applicability_evidence": scope}


def CLI_CASES(context):
    scope = json.loads((Path(__file__).parent/"cli-scope.json").read_text())
    required = scope["benchmark_driver"]["registration_condition"]["required_library_callbacks"]
    missing = [name for name in required if not callable(GATES.get(name))]
    if missing:
        # Shared inapplicable() maps absent evidence to pending; the CPU
        # correctness executor consequently retains unmeasured, not N/A.
        return {"format": "judge-generic-cli-cases-v1", "applicable": False,
                "reason": "Unmeasured: covering library callbacks missing: "+", ".join(missing),
                "applicability_evidence": {},
                "missing_covering_callbacks": missing}
    scope["benchmark_driver"]["registered_covering_callbacks"] = required
    return {"format": "judge-generic-cli-cases-v1", "applicable": False,
            "reason": scope["public_markdown_cli"]["reason"],
            "applicability_evidence": scope,
            "workload_effects": scope["benchmark_driver"]}


# Synthetic driver OS file operations are not public library APIs. The exclusion
# is conditional on covering library registration; gate success and lifecycle
# classification remain entirely central and are never inferred from presence.
GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process,
         "aliasing": aliasing}
