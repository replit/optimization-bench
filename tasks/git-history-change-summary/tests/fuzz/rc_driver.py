"""Native same-process xdiff facts. No task-side acceptance decisions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
TASK = "git-history-change-summary"
SURFACE = {
    "id": "git.xdiff.diff-and-merge",
    "visibility": "public",
    "contract_id": "git-task-preserve-xdiff-callbacks-ownership-v1",
    "precondition_id": "git-bounded-initialized-mmfiles-v1",
    "oracle": {
        "baseline_terminations": ["return"],
        "baseline_exit_codes": [0],
        "required_error_fields": {"code": "integer", "category": "nonempty_string"},
    },
}
CASES = [
    (0, 0, 0), (0, 1, 0), (0, 16384, 0), (0, 32768, 0),
    (0, 2, 0), (1, 0, 0), (2, 32768, 0), (0, 0, 1),
    (0, 16384, 1), (0, 32768, 1), (3, 0, 0),
]


def digest(value):
    body = {k: v for k, v in value.items() if k != "case_sha256"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def cases():
    return [
        {"task": TASK, "surface_id": SURFACE["id"],
         "contract_id": SURFACE["contract_id"],
         "precondition_id": SURFACE["precondition_id"],
         "input": {"fixture": "rc_probe.c:fixed-documented-mmfiles-v1"},
         "options": {"scenario": s, "flags": f, "fail_callback": e},
         "sequence": ["initialize", "call", "free"],
         "faults": ["out_line-return-negative"] if e else []}
        for s, f, e in CASES
    ]


def compile_probe(build, binary):
    project = build / "project"
    command = ["cc", "-O1", "-g", "-I" + str(project), str(HERE / "rc_probe.c"),
               str(project / "libgit.a"), str(project / "target/release/libgitcore.a"),
               "-lz", "-lssl", "-lcrypto", "-lpcre2-8", "-lcurl", "-lexpat",
               "-lpthread", "-ldl", "-lm", "-o", str(binary)]
    result = subprocess.run(command, capture_output=True)
    (binary.parent / (binary.name + ".compile.stdout")).write_bytes(result.stdout)
    (binary.parent / (binary.name + ".compile.stderr")).write_bytes(result.stderr)
    result.check_returncode()
    return {"argv": command,
            "probe_source_sha256": hashlib.sha256((HERE / "rc_probe.c").read_bytes()).hexdigest(),
            "probe_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}


def run(binary, requests, out, label):
    payload = "".join("{scenario} {flags} {fail_callback}\n".format(**c["options"])
                      for c in requests)
    result = subprocess.run([str(binary)], input=payload.encode(), capture_output=True,
                            timeout=120)
    (out / (label + ".stdout")).write_bytes(result.stdout)
    (out / (label + ".stderr")).write_bytes(result.stderr)
    result.check_returncode()
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != len(requests):
        raise ValueError("native probe transport lost observations")
    return rows


def observe(row, case, build_sha):
    rc = row["return_code"]
    outcome = ({"kind": "error", "code": rc, "category": "xdiff-callback-aborted"}
               if rc < 0 else {"kind": "ok", "value": rc})
    return {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {"task": TASK, "surface_id": SURFACE["id"],
                     "case_sha256": digest(case), "build_sha256": build_sha},
        "termination": {"kind": "return", "exit_code": 0},
        "outcome": outcome,
        "effects": {k: row[k] for k in ("output_hex", "inputs_unchanged",
                                       "retained_output_unchanged", "second_return_code")},
        "diagnostics": {"execution_affinity": row["execution_affinity"],
                        "pid": row["pid"]},
    }


def main():
    parser = argparse.ArgumentParser()
    for arm in ("baseline", "candidate"):
        parser.add_argument("--" + arm, type=Path, required=True)
        parser.add_argument("--" + arm + "-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    items = cases()
    builds = {a: getattr(args, a + "_sha") for a in ("baseline", "candidate")}
    compiled, persistent, fresh = {}, {}, {}
    for arm in builds:
        binary = args.out / (arm + "-probe")
        compiled[arm] = compile_probe(getattr(args, arm), binary)
        # Full varied A/B/.../A sequence in ONE native process for each arm.
        persistent[arm] = run(binary, items + items, args.out, arm + "-persistent")
        fresh[arm] = [
            [run(binary, [case], args.out, f"{arm}-fresh-{i}-{r}")[0]
             for r in range(2)] for i, case in enumerate(items)
        ]
    count = len(items)
    rows = [
        {"case": case, "surface": SURFACE,
         **{a: [observe(persistent[a][i], case, builds[a]),
                observe(persistent[a][i + count], case, builds[a])] for a in builds},
         **{"fresh_" + a: [observe(v, case, builds[a]) for v in fresh[a][i]]
            for a in builds}}
        for i, case in enumerate(items)
    ]
    lifecycle = [
        {"case_id": digest(case),
         **{a: {"available": True, "before": persistent[a][i + r * count]["before"],
                "after": persistent[a][i + r * count]["after"]} for a in builds}}
        for r in range(2) for i, case in enumerate(items)
    ]
    facts = {
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": rows,
        "workers": {a: {"mode": "persistent", "target_library_in_process": True,
                        "worker_id": str(persistent[a][0]["pid"]), "calls": 2 * count}
                    for a in builds},
        "lifecycle": lifecycle, "lifecycle_policy": {"kind": "native-os-threads"},
        "compiled": compiled,
        "limitations": ["bounded fixed cases, not complete API or coverage qualification",
                        "partial/EINTR callbacks are not a documented xdiff stream protocol",
                        "independent process-global-state gate remains controller-owned"],
    }
    (args.out / "repeat-facts.json").write_text(json.dumps(facts, indent=2) + "\n")
    for name, predicate in (
        ("stream-facts.json", lambda c: c["options"]["fail_callback"] == 1),
        ("alias-facts.json", lambda c: c["options"]["scenario"] == 3),
    ):
        selected = [r for r in rows if predicate(r["case"])]
        (args.out / name).write_text(json.dumps(
            {"format": facts["format"], "applicable": True, "cases": selected},
            indent=2) + "\n")
    print(json.dumps({"cases": count, "native_calls_per_persistent_worker": 2 * count}))


if __name__ == "__main__":
    main()
