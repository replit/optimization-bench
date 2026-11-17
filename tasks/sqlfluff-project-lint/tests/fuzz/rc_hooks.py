"""Verifier-owned facts for the canonical central RC classifier."""
import json
from pathlib import Path
import shutil
import tempfile

from .adapter import precondition as legacy_precondition, compare as legacy_compare
from . import rc_api_cases
from .rc_cli import requests as CLI_CASES, precondition as CLI_PRECONDITION
from .rc_cli import header_request as HEADER_CONSUMERS


def PRECONDITION(case, surface):
    if case.get("precondition_id") == rc_api_cases.DOMAIN:
        return rc_api_cases.precondition(case, surface)
    return legacy_precondition(case, surface)


def COMPARE(before, after, case):
    if case.get("precondition_id") == rc_api_cases.DOMAIN:
        return rc_api_cases.compare(before, after, case)
    return legacy_compare(before, after, case)


def aliasing(context):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "sqlfluff-public-aliasing"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sqlfluff-rc-") as temporary:
        scratch = Path(temporary)
        ctx = Context(scratch, Path(context["candidate"]),
                      task / "environment/workload/inputs", scratch,
                      task / "environment/workload", docker=True)
        executor = DockerExecutor(context["image"], context["cpuset"],
            extra_docker_flags=[
                *HARDENING,
                "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
            ], tmpfs="/tmp:rw,nosuid,nodev,size=1g")
        steps = executor.run([Step([
            "python3", "/verifier-fuzz/rc_driver.py",
            "--baseline", "/verifier-baseline", "--candidate", "/app/build",
            "--baseline-sha", context["builds"]["baseline"],
            "--candidate-sha", context["builds"]["candidate"],
            "--out", "/scratch/facts",
        ], timeout=300)], ctx, {})
        for index, result in enumerate(steps):
            (output / f"step-{index}.stdout").write_bytes(result.stdout)
            (output / f"step-{index}.stderr").write_bytes(result.stderr)
        if any(result.exit_code != 0 for result in steps):
            raise ValueError("SQLFluff RC transport failed; inspect retained streams")
        shutil.copytree(scratch / "facts", output / "facts", dirs_exist_ok=True)
        (output / "execution.json").write_text(json.dumps({
            "image": context["image"], "cpuset": context["cpuset"], "builds": context["builds"],
            "steps": [{"exit_code": result.exit_code, "leftovers": result.leftovers,
                       "telemetry": result.telemetry} for result in steps],
        }, indent=2, sort_keys=True) + "\n")
    return json.loads((output / "facts/aliasing-facts.json").read_text())


def _api_facts(context, filename):
    from judge import verifier
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "sqlfluff-public-api"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["builds"]["baseline"],
           context["builds"]["candidate"], context["cpuset"], context["image"])
    cache = context.setdefault("_sqlfluff_api_facts", {})
    if cache.get("key") != key:
        shared = Path(verifier.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="sqlfluff-api-rc-") as directory:
            scratch = Path(directory)
            ctx = Context(scratch, Path(context["candidate"]),
                          task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[
                    *verifier.HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
                    "-v", f"{shared / 'tools/fuzz_api_faults'}:/verifier-shared/tools/fuzz_api_faults:ro",
                ], tmpfs="/tmp:rw,nosuid,nodev,size=1g")
            results = executor.run([Step([
                "python3", "/verifier-fuzz/rc_api_facts.py",
                "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                "--baseline-sha", context["builds"]["baseline"],
                "--candidate-sha", context["builds"]["candidate"],
                "--out", "/scratch/facts",
            ], timeout=600)], ctx, {"PYTHONPATH": "/verifier-shared"})
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 for result in results):
                raise ValueError("SQLFluff public API transport failed; inspect retained streams")
            shutil.copytree(scratch / "facts", output / "facts", dirs_exist_ok=True)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"], "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results],
            }, indent=2, sort_keys=True) + "\n")
        cache["key"] = key
    return json.loads((output / "facts" / filename).read_text())


def stream_faults(context):
    return _api_facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _api_facts(context, "repeat-facts.json")


GATES = {"aliasing": aliasing, "stream_faults": stream_faults,
         "repeat_in_process": repeat_in_process}
