"""Task requests/facts only; no local gate verdict or qualification policy."""
import json
from pathlib import Path
import shutil
import tempfile

from .rc_cases import PRECONDITION, COMPARE
from .rc_cli import CLI_CASES, CLI_PRECONDITION

ROOT = Path(__file__).resolve().parent


def HEADER_CONSUMERS(context):
    evidence = json.loads((ROOT / "header-scope.json").read_text())
    lock = json.loads((ROOT.parent.parent / "environment/source.lock.json").read_text())["upstream"]
    if evidence["source_commit"] != lock["commit"] or evidence["archive_sha256"] != lock["sha256"]:
        raise ValueError("Header applicability evidence does not match pinned upstream")
    return {"format": "judge-generic-header-request-v1", "applicable": False,
            "reason": "Authenticated pinned JS/TS package has no public C/C++ headers or native units.",
            "applicability_evidence": evidence}


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor
    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "terser-public-library"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["image"], context["cpuset"],
           context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_terser_rc_facts", {})
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="terser-rc-") as directory:
            scratch = Path(directory)
            ctx = Context(scratch, Path(context["candidate"]),
                          task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[*HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro"],
                tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            argv = ["python3", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"],
                    "--out", "/scratch/facts"]
            results = executor.run([Step(argv, timeout=300)], ctx, {})
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if (scratch / "facts").is_dir():
                shutil.copytree(scratch / "facts", output / "raw", dirs_exist_ok=True)
            if len(results) != 1 or any(result.exit_code or result.leftovers for result in results):
                raise ValueError("Terser isolated RC worker failed; retained raw streams")
            for name in ("repeat-facts.json", "alias-facts.json", "stream-facts.json", "build-receipt.json"):
                shutil.copy2(scratch / "facts" / name, output / name)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "requested_cpuset": context["cpuset"],
                "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results]}, sort_keys=True) + "\n")
        cache["key"] = key
    return json.loads((output / filename).read_text())


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


def stream_faults(context):
    return _facts(context, "stream-facts.json")


GATES = {"repeat_in_process": repeat_in_process, "aliasing": aliasing, "stream_faults": stream_faults}
