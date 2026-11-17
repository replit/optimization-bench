"""Complete-result and ownership checks for an expanded existing conversion unit.

Private execution is checksum-only. Never print input/output bodies or identities.
No timing-stability sampling or scoring-policy changes happen here.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pstats
import shutil
import statistics
import subprocess
import sys
import sysconfig
import time
import traceback

TASK = Path(__file__).resolve().parents[2]
WORKLOAD = TASK / "environment/workload"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def expected(case, values):
    return set(values) == {doc["path"] for doc in case.data["docs"]} and all(
        (len(values[doc["path"]]), hashlib.sha256(values[doc["path"]]).hexdigest())
        == (doc["expect_len"], doc["expect_sha"]) for doc in case.data["docs"])


def execute(runner, module, case, build, inputs, directory, profile=False, missing=False):
    directory.mkdir(parents=True)
    ctx = runner.Context(directory, build, inputs, Path("/app/projects"), WORKLOAD, seed=1729)
    steps = module.prepare(case, ctx)
    assert len(steps) == 1 and not steps[0].resident
    if profile:
        steps[0].argv = [sys.executable, "-m", "cProfile", "-o", str(directory / "profile.pstats"),
                         *steps[0].argv[1:]]
    start = time.perf_counter()
    results = runner.LocalExecutor().run(steps, ctx, module.ENV)
    duration = time.perf_counter() - start
    assert all(r.exit_code == 0 and not r.stdout and not r.stderr and not r.leftovers for r in results)
    values = module.collect(case, ctx, results)
    if missing:
        path = directory / (case.data["docs"][0]["path"] + ".out")
        saved = path.read_bytes()
        path.unlink()
        assert not expected(case, module.collect(case, ctx, results))
        path.write_bytes(saved)
    return values, duration


def ownership(profile, runtime):
    totals, rows = Counter(), []
    for (file, line, name), (primitive, calls, own, _, _) in pstats.Stats(str(profile)).stats.items():
        if file.startswith(str(runtime / "sqlglot") + "/"):
            category = "repository"
        elif "/site-packages/" in file or "/dist-packages/" in file:
            category = "dependency"
        elif file == "~":
            category = "builtin"
        elif file.startswith("<frozen ") or file.startswith(sysconfig.get_path("stdlib") + "/"):
            category = "stdlib"
        elif file.startswith(str(WORKLOAD) + "/"):
            category = "driver"
        else:
            category = "unresolved"
        totals[category] += own
        rows.append({"file": file, "line": line, "function": name, "calls": calls,
                     "primitive_calls": primitive, "self_seconds": own, "category": category})
    rows.sort(key=lambda row: row["self_seconds"], reverse=True)
    profile.with_suffix(".functions.json").write_text(json.dumps(rows, indent=2))
    repo, dependency, unknown = (totals[key] for key in ("repository", "dependency", "unresolved"))
    return {"method": "Robin-accepted cProfile Python self time, default wall timer",
            "approved_ratio": repo / (repo + dependency),
            "conservative_python_ratio": repo / (repo + dependency + unknown),
            "strict_ratio": repo / sum(totals.values()), "self_seconds": dict(totals),
            "native_cpu_claim": False}


def control(baseline, directory, family, label):
    source = directory / "source"
    shutil.copytree(baseline / "runtime/sqlglot", source / "sqlglot")
    path = source / "sqlglot/__init__.py"
    name = "transpile"
    body = ["", "import functools as _control_functools",
            f"_control_original = {name}", "@_control_functools.wraps(_control_original)",
            f"def {name}(*args, **kwargs):"]
    if label == "skip":
        body += ["    return []"]
    else:
        body += ["    result = _control_original(*args, **kwargs)"]
        if label == "wrong":
            body += ["    result = list(result)",
                     "    result = [result[0] + ' -- deliberately wrong', *result[1:]] if result else ['wrong']"]
        else:
            iterations = 100_000
            body += ["    checksum = 0", f"    for index in range({iterations}):",
                     "        checksum = (checksum + index) % 65521"]
        body += ["    return result"]
    path.write_text(path.read_text() + "\n".join(body) + "\n")
    build = directory / "build"
    subprocess.run(["sh", str(WORKLOAD / "build.sh"), str(build)], check=True,
                   env={**os.environ, "SQLGLOT_SOURCE": str(source)},
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return build


def main(args):
    runner = load("runner", WORKLOAD / "runner.py")
    module = load("analysis_task", WORKLOAD / "task.py")
    author = load("analysis_author_baseline", TASK / "public/optimizer/prepare.py")
    author.baseline(args.baseline / "runtime")
    summary = {"mode": "development checks, not A/A or qualification", "scopes": {}}
    cohorts = [("public", WORKLOAD / "inputs")]
    if args.private:
        cohorts.append(("private", TASK / "tests/heldout"))
    for scope, inputs in cohorts:
        cases = module.cases(inputs)
        scope_result = {}
        for case in cases:
            result, _ = execute(runner, module, case, args.baseline, inputs,
                                args.output / scope / case.id / "correctness", missing=True)
            assert expected(case, result)
            scope_result[case.id] = {"complete_outputs_match": True, "cases": len(case.data["docs"]),
                                     "missing_output_rejected": True}
            if case.id != "batch":
                continue
            times = []
            for index in range(3):
                result, elapsed = execute(runner, module, case, args.baseline, inputs,
                                          args.output / scope / case.id / f"normal-{index}")
                assert expected(case, result)
                times.append(elapsed)
            profile_dir = args.output / scope / case.id / "profile"
            result, _ = execute(runner, module, case, args.baseline, inputs, profile_dir, profile=True)
            assert expected(case, result)
            scope_result[case.id].update(ownership(profile_dir / "profile.pstats", args.baseline / "runtime"))
            rows = json.loads((profile_dir / "profile.functions.json").read_text())
            calls = sum(row["calls"] for row in rows
                        if row["file"] == str(args.baseline / "runtime/sqlglot/__init__.py")
                        and row["function"] == "transpile")
            assert calls == len(case.data["docs"]), "Each distinct file must run exactly once"
            scope_result[case.id]["transpile_calls"] = calls
            assert scope_result[case.id]["approved_ratio"] >= 0.5, "Ownership below the accepted threshold"
            scope_result[case.id].update(cold_seconds=times, median_seconds=statistics.median(times),
                                          all_at_least_1_3_seconds=min(times) >= 1.3)
            if scope == "private":
                assert min(times) >= 1.3, "Expanded private unit below the sandbox target"
        summary["scopes"][scope] = scope_result
        (args.output / "aggregate.json").write_text(json.dumps(summary, indent=2))
    for family in ("batch",):
        for label in ("wrong", "skip", "slow"):
            build = control(args.baseline, args.output / "controls" / family / label, family, label)
            for scope, inputs in cohorts:
                unaffected = {}
                for case in module.cases(inputs):
                    result, elapsed = execute(runner, module, case, build, inputs,
                                              args.output / scope / family / label / case.id)
                    matches = expected(case, result)
                    assert matches == (label == "slow" or case.id != family)
                    if case.id == family:
                        assert all(type(json.loads(value)) is list and
                                   all(type(item) is str for item in json.loads(value))
                                   for value in result.values())
                        if label == "slow":
                            assert elapsed >= summary["scopes"][scope][family]["median_seconds"] * 1.25
                        summary["scopes"][scope][family][label] = {
                            "complete_outputs_match": matches, "seconds": elapsed}
                    else:
                        unaffected[case.id] = matches
                summary["scopes"][scope][family][label]["other_units_match"] = unaffected
                (args.output / "aggregate.json").write_text(json.dumps(summary, indent=2))
    summary["all_controls_passed"] = True
    (args.output / "aggregate.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    args.output.chmod(0o700)
    try:
        with (args.output / "details.log").open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            main(args)
    except Exception:
        with (args.output / "details.log").open("a") as log:
            traceback.print_exc(file=log)
        raise SystemExit("Checks failed, diagnostics retained privately")
    print("Conversion correctness, ownership and rebuilt controls passed")
