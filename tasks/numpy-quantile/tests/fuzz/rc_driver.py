"""Produce numpy-quantile RC facts inside the pinned task image (verifier-owned).

Outputs (facts only; the central controller assigns verdicts):
  repeat-facts.json  persistent same-process A/B/A calls + fresh-process oracle,
                     per-call fds/threads(+identities)/children lifecycle rows
  alias-facts.json   public result independence: mutate a returned result, call
                     again, then make a later unrelated call (fresh processes)
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from judge.fuzzing_contract import case_digest

HERE = Path(__file__).resolve().parent
TASK = "numpy-quantile"
CONTRACT = "numpy-quantile/instruction#workload-and-correctness"
PRECOND = "public-documented-call"
REQUIRED_ERRORS = {"class": "nonempty_string", "message": "string"}


def surface(func):
    return {"id": f"numpy.{func}", "visibility": "public", "contract_id": CONTRACT,
            "precondition_id": PRECOND, "oracle_policy": "numpy-quantile/protected-8eps-v1",
            "oracle": {"baseline_terminations": ["return"], "baseline_exit_codes": [0],
                       "required_error_fields": REQUIRED_ERRORS}}


def case(func, inp, options, sequence):
    value = {"task": TASK, "surface_id": f"numpy.{func}", "contract_id": CONTRACT,
             "precondition_id": PRECOND, "input": inp, "options": options,
             "sequence": sequence, "faults": None}
    value["case_sha256"] = case_digest(value)
    return value


def inp(shape, dtype="float64", kind="normal", layout="C", seed=1729):
    return {"shape": list(shape), "dtype": dtype, "kind": kind, "layout": layout, "seed": seed}


def repeat_cases():
    q3 = [0.1, 0.5, 0.9]
    return [
        case("quantile", inp([70001]), {"q": q3}, "repeat"),
        case("quantile", inp([4000, 6], kind="nan_some"), {"q": q3, "axis": 0}, "repeat"),
        case("quantile", inp([6, 70000], "float32"), {"q": [0.25, 0.75], "axis": 1, "keepdims": True}, "repeat"),
        case("quantile", inp([300, 7], layout="F"), {"q": 0.37, "axis": None}, "repeat"),
        case("quantile", inp([257, 9], layout="strided"), {"q": q3, "axis": 0, "method": "nearest"}, "repeat"),
        case("quantile", inp([2048], kind="signed_zeros"), {"q": q3}, "repeat"),
        case("quantile", inp([500], kind="ties", layout="readonly"), {"q": q3, "method": "midpoint"}, "repeat"),
        case("quantile", inp([100]), {"q": 1.5}, "repeat"),  # documented ValueError path
        case("quantile", inp([10, 10]), {"q": q3, "axis": 2}, "repeat"),  # AxisError path
        case("percentile", inp([3000, 4]), {"q": [5.0, 50.0, 95.0], "axis": 0}, "repeat"),
        case("median", inp([65537, 2]), {"axis": 0}, "repeat"),
        case("nanquantile", inp([5000, 3], kind="nan_some"), {"q": q3, "axis": 0}, "repeat"),
        case("nanmedian", inp([999], kind="nan_some"), {}, "repeat"),
        case("quantile", inp([1000], kind="inf_mix"), {"q": q3}, "repeat"),
    ]


def alias_cases():
    q3 = [0.1, 0.5, 0.9]
    return [
        case("quantile", inp([70001]), {"q": q3}, "alias"),
        case("quantile", inp([4000, 6]), {"q": q3, "axis": 0}, "alias"),
        case("quantile", inp([6, 70000], "float32"), {"q": [0.25, 0.75], "axis": 1}, "alias"),
        case("quantile", inp([300, 7]), {"q": 0.5, "axis": 0, "keepdims": True}, "alias"),
        case("percentile", inp([3000, 4]), {"q": [5.0, 95.0], "axis": 1}, "alias"),
        case("median", inp([2049, 3]), {"axis": 0}, "alias"),
        case("nanquantile", inp([5000, 3], kind="nan_some"), {"q": q3, "axis": 0}, "alias"),
    ]


def protocol_cases():
    """Public array_like callback protocols (__array__, __array_function__) raising
    OSError(EPIPE/ENOSPC), plus healthy __array__ controls, on all 6 public entry points."""
    calls = [("quantile", {"q": [0.1, 0.5, 0.9]}), ("nanquantile", {"q": [0.25, 0.75]}),
             ("percentile", {"q": [5.0, 95.0]}), ("nanpercentile", {"q": 50.0}),
             ("median", {}), ("nanmedian", {})]
    out = []
    for func, opts in calls:
        for proto in ("array", "array_function"):
            for code in ("EPIPE", "ENOSPC"):
                out.append(case(func, {**inp([257]), "protocol": proto, "errno": code}, opts, "protocol_fault"))
        out.append(case(func, {**inp([257]), "protocol": "array"}, opts, "protocol_healthy"))
    return out


def fresh(site, value, build_sha, scratch):
    path = scratch / f"case-{value['case_sha256'][:16]}.json"
    path.write_text(json.dumps(value))
    out = subprocess.run([sys.executable, str(HERE / "rc_worker.py"), site, "fresh", str(path), build_sha],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"fresh worker failed ({out.returncode}): {out.stderr[-500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def persistent(site, cases, build_sha, passes=3):
    proc = subprocess.Popen([sys.executable, str(HERE / "rc_worker.py"), site, "persistent", build_sha],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    hello = json.loads(proc.stdout.readline())
    rows = []
    for index in range(passes):
        for value in cases:  # A/B/A: other cases interleave between repeats
            proc.stdin.write(json.dumps(value) + "\n"); proc.stdin.flush()
            rows.append((index, value["case_sha256"], json.loads(proc.stdout.readline())))
    proc.stdin.close()
    if proc.wait(timeout=120) != 0:
        raise RuntimeError("persistent worker exited nonzero")
    return hello, rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--baseline-sha", required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    scratch = a.out / "cases"; scratch.mkdir(exist_ok=True)
    sites = {"baseline": a.baseline.rstrip("/") + "/site", "candidate": a.candidate.rstrip("/") + "/site"}
    shas = {"baseline": a.baseline_sha, "candidate": a.candidate_sha}
    cases = repeat_cases()
    workers, persisted = {}, {}
    for arm in ("baseline", "candidate"):
        hello, rows = persistent(sites[arm], cases, shas[arm])
        workers[arm] = {"mode": "persistent", "target_library_in_process": True,
                        "worker_id": f"{arm}-pid{hello['pid']}", "calls": len(rows),
                        "affinity": hello["affinity"], "numpy": hello["numpy"]}
        persisted[arm] = rows
    rows_out, lifecycle = [], []
    for value in cases:
        sha = value["case_sha256"]
        entry = {"case": value, "surface": surface(value["surface_id"].split(".")[1])}
        for arm in ("baseline", "candidate"):
            entry[arm] = [r["observation"] for _, s, r in persisted[arm] if s == sha]
            entry["fresh_" + arm] = [fresh(sites[arm], value, shas[arm], scratch)["observation"] for _ in range(2)]
        rows_out.append(entry)
    for (i, sha, b), (_, sha2, c) in zip(persisted["baseline"], persisted["candidate"]):
        assert sha == sha2
        lifecycle.append({"case_id": sha, "pass": i, "baseline": b["lifecycle"], "candidate": c["lifecycle"]})
    (a.out / "repeat-facts.json").write_text(json.dumps({
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": rows_out,
        "workers": workers, "lifecycle": lifecycle,
        "affinity_os_ids": {"driver": sorted(os.sched_getaffinity(0))}}, indent=1, sort_keys=True) + "\n")
    alias_rows = []
    for value in alias_cases():
        entry = {"case": value, "surface": surface(value["surface_id"].split(".")[1])}
        for arm in ("baseline", "candidate"):
            entry[arm] = [fresh(sites[arm], value, shas[arm], scratch)["observation"] for _ in range(2)]
        alias_rows.append(entry)
    stream_rows = []
    for value in protocol_cases():
        entry = {"case": value, "surface": surface(value["surface_id"].split(".")[1])}
        for arm in ("baseline", "candidate"):
            entry[arm] = [fresh(sites[arm], value, shas[arm], scratch)["observation"] for _ in range(2)]
        stream_rows.append(entry)
    (a.out / "stream-facts.json").write_text(json.dumps({
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": stream_rows,
        "scope": {"class": "public array_like user-callback protocols",
                  "protocols": ["__array__", "__array_function__"],
                  "faults": ["OSError(EPIPE) -> BrokenPipeError", "OSError(ENOSPC)"],
                  "not_applicable_part": "no literal reader/writer/file parameter in the public signatures"}},
        indent=1, sort_keys=True) + "\n")
    (a.out / "alias-facts.json").write_text(json.dumps({
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": alias_rows},
        indent=1, sort_keys=True) + "\n")
    print(json.dumps({"repeat_cases": len(rows_out), "lifecycle_rows": len(lifecycle),
                      "alias_cases": len(alias_rows), "stream_cases": len(stream_rows), "workers": workers}))


if __name__ == "__main__":
    main()
