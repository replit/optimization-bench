#!/usr/bin/env python3
"""Compile matching-arm native observers and emit facts, never gate verdicts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
TASK = "sqlite-distinct-plant-facets"
SURFACE = {
    "id": "sqlite3.public.vfs-sql", "visibility": "public",
    "contract_id": "sqlite3-public-c-api-v1", "precondition_id": "sqlite3-rc-bounded-v1",
    "oracle": {"baseline_terminations": ["return"], "baseline_exit_codes": [0],
               "required_error_fields": {"code": "integer", "extended_code": "integer",
                                         "message": "nonempty_string"}},
}
ALIAS_SURFACE = {**SURFACE, "id": "sqlite3.public.pointer-lifetimes"}
OPERATIONS = ("facets", "search", "upsert", "vacuum")
FAULTS = ("healthy", "short", "read_eio", "sys_eio", "sys_short", "no_page",
          "full", "sync", "truncate")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def cases():
    rows = []
    for operation in OPERATIONS:
        modes = ["healthy", "short", "read_eio", "sys_eio", "sys_short", "no_page"]
        if operation in ("upsert", "vacuum"):
            modes += ["full", "sync"]
        if operation == "vacuum":
            modes += ["truncate"]
        for mode in modes:
            for mmap in (0, 1):
                # Include differing connection data in A/B/A to expose static caches.
                for seed in (0, 17):
                    rows.append(make_case(operation, mode, mmap, seed))
    return rows


def make_case(operation, fault="healthy", mmap=0, seed=0):
    surface = ALIAS_SURFACE if operation == "alias" else SURFACE
    case = {"task": TASK, "surface_id": surface["id"],
            "contract_id": surface["contract_id"], "precondition_id": surface["precondition_id"],
            "operation": operation, "fault": fault, "mmap": mmap, "seed": seed}
    case["case_sha256"] = digest(case)
    return case


def compile_probe(build, binary):
    objects = build / "objects"
    argv = ["cc", "-O2", "-Wall", "-Wextra", "-I" + str(objects), str(HERE / "rc_probe.c"),
            str(objects / "libsqlite3.a"), "-lm", "-ldl", "-lpthread", "-o", str(binary)]
    result = subprocess.run(argv, capture_output=True)
    binary.with_suffix(".compile.stdout").write_bytes(result.stdout)
    binary.with_suffix(".compile.stderr").write_bytes(result.stderr)
    result.check_returncode()
    return {"argv": argv, "probe_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "source_sha256": hashlib.sha256((HERE / "rc_probe.c").read_bytes()).hexdigest(),
            "library_sha256": hashlib.sha256((objects / "libsqlite3.a").read_bytes()).hexdigest()}


def snapshot(pid):
    root = Path("/proc") / str(pid)
    identities, children = [], set()
    for entry in sorted((root / "task").iterdir()):
        fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        identities.append({"tid": int(entry.name), "start_ticks": int(fields[19]),
                           "state": fields[0], "scope_pid": pid})
        children_file = entry / "children"
        if children_file.exists():
            children.update(children_file.read_text().split())
        else:
            # Some container kernels omit CONFIG_CHECKPOINT_RESTORE's children
            # file. Enumerate actual PPids instead, never substitute zero.
            for proc in Path("/proc").iterdir():
                if not proc.name.isdigit():
                    continue
                try:
                    stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                except FileNotFoundError:
                    continue  # unrelated process exited during the census
                if int(stat[1]) == int(entry.name):
                    children.add(proc.name)
    return {"fds": len(list((root / "fd").iterdir())), "threads": len(identities),
            "children": len(children), "thread_identities": identities}


def run(binary, sequence, directory):
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    with (directory / "stderr").open("wb") as error:
        process = subprocess.Popen([str(binary)], cwd=directory, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=error, text=True)
        try:
            if process.stdout.readline().strip() != "ready":
                raise RuntimeError("Native observer did not initialize; inspect retained stderr")
            for case in sequence:
                before = snapshot(process.pid)
                process.stdin.write("{operation} {fault} {mmap} {seed}\n".format(**case))
                process.stdin.flush()
                raw = json.loads(process.stdout.readline())
                after = snapshot(process.pid)
                rows.append({"raw": raw, "before": before, "after": after})
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError("Native observer exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
    (directory / "raw.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows


def observation(row, case, build):
    raw = row["raw"]
    error = {k: raw[k] for k in ("code", "extended_code", "message")}
    outcome = ({"kind": "error", **error} if raw["code"] else
               {"kind": "ok", "value": raw["result"]})
    effects = {key: value for key, value in raw.items() if key not in
               ("code", "extended_code", "message", "pid", "execution_affinity", "hits", "reads", "fetches")}
    return {"format": "optimbench-fuzz-observation-v1",
            "identity": {"task": TASK, "surface_id": case["surface_id"],
                         "case_sha256": case["case_sha256"], "build_sha256": build},
            "termination": {"kind": "return", "exit_code": 0}, "outcome": outcome, "effects": effects,
            "diagnostics": {key: raw[key] for key in
                            ("pid", "execution_affinity", "hits", "reads", "fetches")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("baseline", "candidate", "out"):
        parser.add_argument("--" + key, required=True, type=Path)
    for key in ("baseline-sha", "candidate-sha"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    library_cases = cases()
    alias_cases = [make_case("alias", seed=seed) for seed in (0, 17)]
    all_cases = library_cases + alias_cases
    compiled, fresh, persistent, workers = {}, {}, {}, {}
    for arm, identity in builds.items():
        binary = args.out / (arm + "-probe")
        compiled[arm] = compile_probe(getattr(args, arm).resolve(), binary)
        fresh[arm] = []
        for index, case in enumerate(all_cases):
            fresh[arm].append([observation(run(binary, [case],
                args.out / f"{arm}-fresh-{index}-{rep}")[0], case, identity) for rep in range(2)])
        # Whole corpus twice gives every A an intervening B, including different databases.
        rows = run(binary, all_cases + all_cases, args.out / f"{arm}-persistent")
        if len({row["raw"]["pid"] for row in rows}) != 1:
            raise RuntimeError("Persistent target process changed")
        persistent[arm] = rows
        workers[arm] = {"mode": "persistent", "target_library_in_process": True,
                        "worker_id": f"{arm}:{rows[0]['raw']['pid']}", "calls": len(rows)}
    common = {"format": "judge-generic-call-evidence-v1", "applicable": True, "compiled": compiled}
    rows = [{"case": c, "surface": ALIAS_SURFACE if c["operation"] == "alias" else SURFACE,
             **{arm: fresh[arm][i] for arm in builds}} for i, c in enumerate(all_cases)]
    repeat, lifecycle = [], []
    n = len(all_cases)
    for i, row in enumerate(rows):
        repeat.append({**row, **{arm: [observation(persistent[arm][j], row["case"], builds[arm])
                                       for j in (i, i+n)] for arm in builds},
                       **{"fresh_" + arm: fresh[arm][i] for arm in builds}})
    for i, case in enumerate(all_cases + all_cases):
        lifecycle.append({"case_id": case["case_sha256"], **{
            arm: {"available": True, **{p: persistent[arm][i][p] for p in ("before", "after")}}
            for arm in builds}})
    for name, facts in (
        ("stream", {"cases": rows[:len(library_cases)]}),
        ("alias", {"cases": rows[len(library_cases):]}),
        ("repeat", {"cases": repeat, "workers": workers, "lifecycle": lifecycle,
                    "lifecycle_policy": {"kind": "native-os-threads"}}),
    ):
        (args.out / f"{name}-facts.json").write_text(json.dumps({**common, **facts}, indent=2) + "\n")
    print(json.dumps({"library_cases": len(library_cases), "alias_cases": len(alias_cases)}))


if __name__ == "__main__":
    main()
