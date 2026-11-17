#!/usr/bin/env python3
"""Facts only, inside the shared isolated executor. No candidate-label branches."""
import argparse
import json
from pathlib import Path
import random

from adapter import generate, generate_aliases, case_sha256
from run import compile_probe, observe, observe_batch
from validate import surface_for


def lifecycle(report):
    def point(name):
        raw = report["diagnostics"][name]
        children = raw["descendant_inventory"]
        return {
            **raw, "fds": len(raw["fd_ids"]),
            "children": len(children.get("descendants", [])) if children["available"] else None,
        }
    before, after = point("lifecycle_before"), point("lifecycle_after")
    available = all(p["thread_identity_available"] and p["descendant_inventory"]["available"]
                    for p in (before, after))
    return {"available": available, "before": before, "after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = list(generate(random.Random(1729), {"cases": 32}))
    # Exact mandatory archived class: a user Serialize callback must observe
    # the first writer's Other error before its continuation can run.
    cases[0]["faults"]["error_kind"] = "Other"
    library_count = len(cases)
    cases.extend(generate_aliases(random.Random(2718)))
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    binaries, compiled, fresh, repeated, workers = {}, {}, {}, {}, {}
    for arm, build in (("baseline", args.baseline), ("candidate", args.candidate)):
        binary = args.out / (arm+"-probe")
        compiled[arm] = compile_probe(build, binary)
        binaries[arm] = binary
        fresh[arm] = [[observe(binary, case, builds[arm]) for _ in range(2)] for case in cases]
        repeated[arm] = observe_batch(binary, cases+cases, builds[arm])
        first = repeated[arm][0]["diagnostics"]["lifecycle_before"]
        identities = first["thread_identities"]
        workers[arm] = {
            "mode": "persistent", "target_library_in_process": True,
            "worker_id": str(identities[0]["scope_pid"]) + ":" + str(identities[0]["start_ticks"]),
            "calls": len(repeated[arm]),
            "build_sha256": builds[arm],
        }
    rows, repeat_rows, lifecycles = [], [], []
    n = len(cases)
    for index, case in enumerate(cases):
        row = {"case": case, "surface": surface_for(case),
               "baseline": fresh["baseline"][index], "candidate": fresh["candidate"][index]}
        rows.append(row)
        repeat_rows.append({
            "case": case, "surface": surface_for(case),
            **{arm: [repeated[arm][index], repeated[arm][index+n]] for arm in builds},
            **{"fresh_"+arm: fresh[arm][index] for arm in builds},
        })
        for repetition in range(2):
            lifecycles.append({
                "case_id": case_sha256(case), "repetition": repetition,
                **{arm: lifecycle(repeated[arm][index+repetition*n]) for arm in builds},
            })
    save = lambda name, data: (args.out/name).write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    save("stream-facts.json", {"format":"judge-generic-call-evidence-v1",
                             "applicable":True, "cases":rows[:library_count], "compiled":compiled})
    save("alias-facts.json", {"format":"judge-generic-call-evidence-v1",
                            "applicable":True, "cases":rows[library_count:], "compiled":compiled})
    save("repeat-facts.json", {"format":"judge-generic-call-evidence-v1",
                             "applicable":True, "cases":repeat_rows, "workers":workers,
                             "lifecycle":lifecycles, "compiled":compiled})
    print(json.dumps({"cases":len(cases), "same_process_calls_per_arm":2*n}), flush=True)


if __name__ == "__main__":
    main()
