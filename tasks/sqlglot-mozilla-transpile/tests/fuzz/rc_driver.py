#!/usr/bin/env python3
"""Isolated fresh and persistent public SQLGlot library observations, not verdicts."""
import argparse
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import make_case, digest, precondition
from driver import observe, Capture, process_state, environment, environment_changes

ROOT = Path(__file__).resolve().parent


def lifecycle():
    pid = int(Path("/proc/self/stat").read_text().split(" ", 1)[0])
    tasks = []
    for path in sorted(Path("/proc/self/task").iterdir()):
        stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
        tasks.append({"tid": int(path.name), "scope_pid": pid,
                      "start_ticks": int(stat[19]), "state": stat[0]})
    # /proc/<pid>/task/<tid>/children is optional on the judge kernel.
    # Derive concrete descendants from live process PPIDs instead.
    parents = {}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(path.name)] = int(stat[1])
            except FileNotFoundError:
                continue  # Process exited during this live snapshot.
    children = set()
    frontier = {pid}
    while frontier:
        frontier = {child for child, parent in parents.items()
                    if parent in frontier and child not in children and child != pid}
        children.update(frontier)
    return {"fds": len(os.listdir("/proc/self/fd")), "threads": len(tasks),
            "children": len(children), "thread_identities": tasks}


def worker(runtime, build, cases, output):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(runtime.resolve()))
    before = process_state()
    before_env = environment()
    stdout, stderr, capture = io.StringIO(), io.StringIO(), Capture()
    logger = logging.getLogger("sqlglot")
    logger.addHandler(capture)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            import sqlglot
    finally:
        logger.removeHandler(capture)
    if not Path(sqlglot.__file__).resolve().is_relative_to(runtime.resolve()):
        raise ValueError("Protected arm runtime did not load")
    import_effects = {
        "stdout_utf8": stdout.getvalue(), "stderr_utf8": stderr.getvalue(),
        "log_messages": capture.rows, "process_before": before, "process_after": process_state(),
        "environment_changes": environment_changes(before_env),
    }
    observations = []
    for case in cases:
        start = lifecycle()
        result = observe(case, sqlglot, build)
        end = lifecycle()
        result["effects"]["import_effects"] = import_effects
        result["diagnostics"].update(
            execution_affinity=sorted(os.sched_getaffinity(0)),
            lifecycle={"available": True, "before": start, "after": end})
        observations.append(result)
    output.write_text(json.dumps(observations, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--baseline-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worker-runtime", type=Path)
    parser.add_argument("--worker-build")
    parser.add_argument("--worker-cases", type=Path)
    args = parser.parse_args()
    if args.worker_runtime:
        worker(args.worker_runtime, args.worker_build,
               json.loads(args.worker_cases.read_text()), args.out)
        return
    args.out.mkdir(parents=True, exist_ok=False)
    specs = (
        ("sqlglot.transpile", "SELECT 1 AS a"),
        ("sqlglot.parse_one", "SELECT ("),
        ("sqlglot.expressions.Expression.copy", "SELECT a FROM t"),
        ("sqlglot.expressions.Expression.transform", "SELECT 1 AS a"),
    )
    cases = [make_case(symbol, sql) for symbol, sql in specs]
    surfaces = {surface["id"]: surface for surface in
                json.loads((ROOT / "surface.json").read_text())["surfaces"]}
    for case in cases:
        case["sequence"] = ["invoke"]
    callback_start = len(cases)
    for symbol, number, copy in (
        ("sqlglot.expressions.Expression.transform", 28, True),
        ("sqlglot.expressions.Expression.transform", 32, False),
        ("sqlglot.expressions.Expression.walk", 28, True),
        ("sqlglot.expressions.Expression.walk", 32, True),
    ):
        case = make_case(symbol, "SELECT a + 1 AS b FROM t")
        case["sequence"] = ["invoke"]
        case["callback_fault"] = {"errno": number, "after": 3, "copy": copy}
        cases.append(case)
    for case in cases:
        if not precondition(case, surfaces[case["surface_id"]])["valid"]:
            raise ValueError("Public RC case failed protected precondition")
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    fresh, repeated, workers = {}, {}, {}

    def execute(arm, tag, values):
        case_path = args.out / f"{arm}-{tag}-cases.json"
        result_path = args.out / f"{arm}-{tag}-observations.json"
        case_path.write_text(json.dumps(values))
        runtime = (args.baseline if arm == "baseline" else args.candidate) / "runtime"
        command = [sys.executable, str(ROOT / "rc_driver.py"), "--worker-runtime", str(runtime),
                   "--worker-build", builds[arm], "--worker-cases", str(case_path),
                   "--out", str(result_path)]
        run = subprocess.run(command, capture_output=True, timeout=30)
        (args.out / f"{arm}-{tag}.stdout").write_bytes(run.stdout)
        (args.out / f"{arm}-{tag}.stderr").write_bytes(run.stderr)
        if run.returncode != 0 or run.stdout or run.stderr:
            raise ValueError("Public SQLGlot worker initialization/transport failed")
        return json.loads(result_path.read_text())

    for arm in builds:
        fresh[arm] = [[execute(arm, f"fresh-{index}-{replay}", [case])[0]
                       for replay in range(2)] for index, case in enumerate(cases)]
        repeated[arm] = execute(arm, "persistent", cases + cases)
        first = repeated[arm][0]["diagnostics"]["lifecycle"]["before"]["thread_identities"][0]
        workers[arm] = {
            "mode": "persistent", "target_library_in_process": True,
            "worker_id": f"{first['scope_pid']}:{first['start_ticks']}",
            "calls": len(repeated[arm]), "build_sha256": builds[arm],
        }
    rows, repeat_rows, lifecycles = [], [], []
    for index, case in enumerate(cases):
        rows.append({"case": case, "surface": surfaces[case["surface_id"]],
                     **{arm: fresh[arm][index] for arm in builds}})
        repeat_rows.append({
            "case": case, "surface": surfaces[case["surface_id"]],
            **{arm: [repeated[arm][index], repeated[arm][index + len(cases)]] for arm in builds},
            **{"fresh_" + arm: fresh[arm][index] for arm in builds},
        })
        for replay in range(2):
            lifecycles.append({
                "case_id": digest(case), "repetition": replay,
                **{arm: repeated[arm][index + replay * len(cases)]["diagnostics"]["lifecycle"]
                   for arm in builds},
            })

    def save(filename, value):
        (args.out / filename).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    save("alias-facts.json", {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": [rows[2], rows[3]],
    })
    save("repeat-facts.json", {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": repeat_rows, "workers": workers, "lifecycle": lifecycles,
    })
    save("stream-facts.json", {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": rows[callback_start:],
        "direct_reader_writer_scope": {
            "scope": "Mapped APIs have no filelike read/write protocol; public transform/prune callbacks "
                     "are exercised with OSError ENOSPC/EPIPE and partial public-tree mutation.",
            "authority": json.loads((ROOT / "receipts/rc-mapped-signatures.json").read_text()),
            "unresolved": "Full transitive public/opts/custom subclass surface remains incomplete.",
        },
    })


if __name__ == "__main__":
    main()
