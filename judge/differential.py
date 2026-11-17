"""Verifier-owned seeded differential cases, isolated by the shared DockerExecutor."""
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Context, Step
from docker_executor import DockerExecutor
try:
    from .probe_build import mounts as probe_build_mounts
    from .probe_lifecycle import script_command
except ImportError:  # Keep direct ``python judge/differential.py`` CLI support.
    from probe_build import mounts as probe_build_mounts
    from probe_lifecycle import script_command


class DifferentialMismatch(ValueError):
    def __init__(self, report):
        super().__init__("Pristine/candidate differential mismatch")
        self.report = report


class Observation(dict):
    """Library observation only; validated provenance is an out-of-band sidecar."""
    def __init__(self, value, preparation):
        super().__init__(value)
        self.receipts = {"container_prepare": preparation}


def receipts(left, right):
    values = {side: value.receipts for side, value in
              (("baseline", left), ("candidate", right)) if getattr(value, "receipts", None)}
    return {"receipts": values} if values else {}


def harness(task):
    path = Path(task) / "tests/differential/generate.py"
    spec = importlib.util.spec_from_file_location("private_differential", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def execute(task, build, image, cpuset, case, timeout):
    folder = Path(task) / "tests/differential"
    config = json.loads((Path(task) / "tests/judge.json").read_text())["differential"]
    with tempfile.TemporaryDirectory(prefix="differential-") as temporary:
        scratch = Path(temporary)
        (scratch / "case.json").write_text(json.dumps(case, ensure_ascii=False))
        ctx = Context(scratch, Path(build).resolve(), scratch, scratch,
                      Path(task).resolve() / "environment/workload", docker=True)
        executor = DockerExecutor(image, cpuset, memory=config.get("memory", "4g"),
            extra_docker_flags=["--cap-drop=ALL", "--security-opt=no-new-privileges",
                                *probe_build_mounts(build),
                                "-v", f"{ROOT / 'judge'}:/verifier-probe:ro",
                                "-v", f"{folder.resolve()}:/verifier-differential:ro"],
            tmpfs="/tmp:rw,nosuid,nodev,size=512m")
        step = Step(script_command(config["command"], "/verifier-probe/probe_lifecycle.py") +
                    ["/scratch/case.json"], timeout=timeout)
        result = executor.run([step], ctx, {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                                          "HOME": "/scratch", "TZ": "UTC", "LC_ALL": "C.UTF-8"})[0]
        if result.leftovers:
            value = {"exit_code": result.exit_code, "leftovers": len(result.leftovers),
                     "leftover_processes": result.leftovers,
                     "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")}
        else:
            try:
                output = json.loads(result.stdout)
            except (ValueError, UnicodeDecodeError):
                output = {"stdout": result.stdout.decode(errors="replace")}
            value = {"exit_code": result.exit_code, "result": output,
                     "stderr": result.stderr.decode(errors="replace"), "leftovers": 0}
        return (Observation(value, executor.container_prepare)
                if isinstance(executor.container_prepare, dict) else value)


def check(task, baseline, candidate, image, cpuset, output, seeds=None):
    task, output = Path(task), Path(output)
    settings = json.loads((task / "tests/judge.json").read_text()).get("differential")
    if not settings or not settings.get("enabled"):
        return {"passed": True, "coverage": "not_implemented", "task": task.name}
    if settings.get("stderr_contract") not in (None, "cli-stderr-contract-v1"):
        raise ValueError("Unsupported differential stderr contract")
    generator = harness(task)
    output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    report = {"task": task.name, "image": image, "passed": True, "seeds": seeds or settings["seeds"],
              "case_budget_per_seed": settings["case_budget"], "time_cap_seconds": settings["time_cap_seconds"],
              "cases": [], "coverage": "seeded differential"}
    def same_observation(left, right):
        if settings.get("stderr_contract") == "cli-stderr-contract-v1":
            from .cli_stderr import normalize_cli_observation
            left, right = normalize_cli_observation(left), normalize_cli_observation(right)
        return generator.equal(left, right) if hasattr(generator, "equal") else left == right
    if settings.get("stderr_contract"):
        report["stderr_contract"] = settings["stderr_contract"]
    for seed in report["seeds"]:
        cases = list(generator.generate(random.Random(seed), settings["case_budget"]))
        assert len(cases) <= settings["case_budget"]
        for index, case in enumerate(cases):
            if time.monotonic() - start > settings["time_cap_seconds"]:
                raise ValueError("Differential budget expired before all declared cases completed")
            timeout = settings.get("case_timeout_seconds", 20)
            left = execute(task, baseline, image, cpuset, case, timeout)
            if left.get("exit_code", 0) or left.get("leftovers", 0):
                (output / "pristine-error.json").write_text(json.dumps({
                    "task": task.name, "seed": seed, "index": index,
                    "case": case, "baseline": left, "image": image,
                }, indent=2, ensure_ascii=False) + "\n")
                raise ValueError("Pristine differential harness failed; case is not qualified")
            right = execute(task, candidate, image, cpuset, case, timeout)
            equal = same_observation(left, right)
            report["cases"].append({"seed": seed, "index": index, "matched": equal, **receipts(left, right)})
            if not equal:
                # Greedy, deterministic shrinking preserves the observed mismatch.
                minimal = case
                for smaller in generator.shrink(case):
                    if time.monotonic() - start > settings["time_cap_seconds"]:
                        break
                    a = execute(task, baseline, image, cpuset, smaller, timeout)
                    b = execute(task, candidate, image, cpuset, smaller, timeout)
                    same = same_observation(a, b)
                    evidence = receipts(a, b)
                    if evidence:
                        report["cases"][-1].setdefault("shrinking_receipts", []).append(
                            {"matched": same, **evidence})
                    if not same:
                        minimal, left, right = smaller, a, b
                reproducer = {"task": task.name, "seed": seed, "case": minimal,
                              "baseline": left, "candidate": right, "image": image, **receipts(left, right)}
                (output / "reproducer.json").write_text(json.dumps(reproducer, indent=2, ensure_ascii=False) + "\n")
                report.update(passed=False, status="differential_mismatch", reproducer="reproducer.json")
                report["elapsed_seconds"] = time.monotonic() - start
                (output / "differential.json").write_text(json.dumps(report, indent=2) + "\n")
                raise DifferentialMismatch(report)
    report["elapsed_seconds"] = time.monotonic() - start
    (output / "differential.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, action="append")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.task, args.baseline, args.candidate, args.image,
                               args.cpuset, args.output, args.seed), indent=2))
    except DifferentialMismatch as error:
        print(json.dumps(error.report, indent=2))
        raise SystemExit(1)
