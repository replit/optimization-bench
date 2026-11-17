#!/usr/bin/env python3
"""Original uploaded collector, retained as an integration-test fixture."""
import argparse, csv, json, os, sys
from pathlib import Path
from remote.run_checks import control_ok

FIELDS = ["run_id", "task", "swarm", "vm", "state", "sha", "reported_sha", "status", "correct", "upstream_gate",
          "public_only", "speedup", "ci_lo", "ci_hi", "optim_score", "aa_full_width_pp", "cpu_ratio", "pairs",
          "provisional", "claimable", "job", "point_speedup", "unit_count", "units_json", "aa_units_json"]

def load(results_dir, vm, job):
    p = os.path.join(results_dir, vm, job, "result.json")
    return json.loads(Path(p).read_text()) if os.path.exists(p) else None

def row_from(r):
    units = r.get("cases") or {}
    b = next(iter(units.values())) if len(units) == 1 else {}
    ci = r.get("candidate_ci95") or b.get("candidate_ci95") or [None, None]
    aa_units = (r.get("aa_precision") or {}).get("units") or {}
    widths = [u["full_width_pp"] for u in aa_units.values() if u.get("full_width_pp") is not None]
    return {"status": r.get("status"), "correct": r.get("correct"), "upstream_gate": r.get("upstream_gate"),
            "public_only": r.get("public_only"), "speedup": r.get("speedup"),
            "point_speedup": r.get("point_speedup"), "unit_count": len(units),
            "units_json": json.dumps(units, sort_keys=True), "aa_units_json": json.dumps(aa_units, sort_keys=True),
            "ci_lo": ci[0], "ci_hi": ci[1], "optim_score": r.get("optim_score"),
            "aa_full_width_pp": max(widths) if widths else None, "cpu_ratio": r.get("cpu_ratio"), "pairs": b.get("pairs"),
            "provisional": r.get("provisional"), "claimable": r.get("claimable")}

def fmt(x):
    return "-" if x is None else f"{float(x):.3f}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("final_json"); ap.add_argument("results_dir"); ap.add_argument("out_csv")
    ap.add_argument("--vms", required=True, help="comma-separated judge VM short names, e.g. intel,amd")
    a = ap.parse_args()
    fin = json.loads(Path(a.final_json).read_text()); results_dir = a.results_dir
    vms = [v.strip() for v in a.vms.split(",") if v.strip()]
    if not vms or len(set(vms)) != len(vms):
        sys.exit("--vms must name distinct VMs")
    present = {d for d in os.listdir(results_dir) if os.path.isdir(os.path.join(results_dir, d))}
    missing = [v for v in vms if v not in present]
    if missing:
        sys.exit(f"no results folder for VM(s) {missing} in {results_dir}")
    if present - set(vms):
        print(f"ignored folders (not in --vms): {sorted(present - set(vms))}", file=sys.stderr)
    tasks = sorted({r["task"] for r in fin["runs"].values()})
    for task in tasks:
        no_ctrl = [v for v in vms if not control_ok(load(results_dir, v, f"{task}--control") or {})]
        if no_ctrl:
            sys.exit(f"{task}: missing or failed pristine control on {no_ctrl}; no campaign score can be reported")
    rows = []
    for run_id, run in fin["runs"].items():
        for vm in vms:
            row = {"run_id": run_id, "task": run["task"], "swarm": run.get("swarm"), "vm": vm, "state": run.get("state"),
                   "sha": run.get("sha"), "reported_sha": run.get("reported_sha"), "job": run.get("job")}
            if run.get("state") == "seed":
                row.update(status="seed", speedup=1.0)
            elif run.get("state") == "candidate":
                r = load(results_dir, vm, run["job"])
                row.update(row_from(r) if r else {"status": "not scored"})
            else:
                row.update(status=run.get("state"))
            rows.append(row)
    for task in tasks:
        for vm in vms:
            r = load(results_dir, vm, f"{task}--control")
            if r:
                rows.append({"run_id": "control", "task": task, "vm": vm, "state": "control", "job": f"{task}--control", **row_from(r)})
    with open(a.out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    units_path = str(Path(a.out_csv).with_suffix("")) + ".units.csv"
    with open(units_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run_id", "task", "vm", "job", "unit", "measurement_json", "aa_json"])
        w.writeheader()
        for row in rows:
            units = json.loads(row.get("units_json") or "{}")
            aa = json.loads(row.get("aa_units_json") or "{}")
            for name in sorted(set(units) | set(aa)):
                w.writerow({**{k: row.get(k) for k in ("run_id", "task", "vm", "job")},
                            "unit": name, "measurement_json": json.dumps(units.get(name)),
                            "aa_json": json.dumps(aa.get(name))})
    print(f"{'run':<28}{'vm':<8}{'status':<14}{'correct':<9}{'speedup':>9}{'ci_lo':>9}{'ci_hi':>9}{'pairs':>7}")
    for r in rows:
        print(f"{r['run_id']:<28}{r['vm']:<8}{str(r.get('status')):<14}{str(r.get('correct', '')):<9}"
              f"{fmt(r.get('speedup')):>9}{fmt(r.get('ci_lo')):>9}{fmt(r.get('ci_hi')):>9}{str(r.get('pairs') or '-'):>7}")
    print(f"\nwrote {len(rows)} rows to {a.out_csv}")
    print(f"wrote all unit diagnostics to {units_path}")
    if any(r["status"] in ("not scored", "running", "not fetched", "error", "invalid run") for r in rows):
        sys.exit(2)

if __name__ == "__main__":
    main()
