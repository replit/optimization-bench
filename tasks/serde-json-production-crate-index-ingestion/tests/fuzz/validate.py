#!/usr/bin/env python3
"""Untimed bounded writer/reader replay; not full API adequacy.

Run inside the unchanged pinned task image with /scratch containing pristine
and candidate builds. All findings are assigned by shared contract code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys

from adapter import TASK, SURFACES, POLICY, canonical, generate, precondition, compare, case_sha256
from run import compile_probe, observe, observe_batch
from judge import fuzzing_contract as contract


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def surface_for(case):
    return {
        "id": case["surface_id"], "visibility": "public", "contract_id": POLICY,
        "precondition_id": "public-typed-json-io-v1", "generators": ["grammar", "faults"],
        "oracle_policy": POLICY,
        "oracle": {"baseline_terminations": ["return"], "baseline_exit_codes": [0],
                   "required_error_fields": {
                       "category": "nonempty_string", "io_kind": ["nonempty_string","null"],
                       "line": ["integer","null"], "column": ["integer","null"]}},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pristine", type=Path, default=Path("/app/baseline-build"))
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    builds = {}
    binaries = {}
    receipts = {}
    for arm, build in (("baseline", args.pristine), ("candidate", args.candidate)):
        binary = args.out / (arm + "-probe")
        receipts[arm] = compile_probe(build, binary)
        builds[arm] = receipts[arm]["libraries"]["serde_json"]
        binaries[arm] = binary
    save(args.out / "builds.json", receipts)
    cases = []
    for seed in (1729, 2718, 31415):
        cases.extend(generate(random.Random(seed), {"cases": 120}))
    corpus = b"".join(canonical(case) + b"\n" for case in cases)
    (args.out / "corpus.jsonl").write_bytes(corpus)
    results = []
    # First qualify pristine repeatability without candidate involvement.
    for case in cases:
        a = observe(binaries["baseline"], case, builds["baseline"])
        b = observe(binaries["baseline"], case, builds["baseline"])
        contract.validate_observation(a, case, builds["baseline"],
                                      surface_for(case)["oracle"]["required_error_fields"])
        if not compare(a, b, case)["equal"] or a["termination"]["kind"] != "return":
            save(args.out / "baseline-invalid.json", {"case": case, "observations": [a,b]})
            raise ValueError("baseline invalid or nonrepeatable")
    # Every mismatch is retained; this does not certify coverage/mutations.
    for case in cases:
        result = contract.evaluate_case(case, surface_for(case), precondition,
            lambda current, arm, index: observe(binaries[arm], current, builds[arm]),
            builds, compare_hook=compare, repetitions=2)
        if result.get("findings"):
            results.append({"case": case, "result": result})
    save(args.out / "findings.json", results)
    # Same-process batches: five repeats under each recorded 1/2/8 affinity.
    available = sorted(os.sched_getaffinity(0))
    matrix = []
    for count in (1, 2, 8):
        if len(available) < count:
            matrix.append({"cpu_count": count, "available": False})
            continue
        for repetition in range(5):
            offset = repetition % (len(available) - count + 1)
            affinity = available[offset:offset+count]
            os.sched_setaffinity(0, affinity)
            for arm in binaries:
                batch = observe_batch(binaries[arm], cases[:32], builds[arm])
                def resource_ids(point):
                    return (point["thread_identities"], point["fd_ids"],
                            point["descendant_inventory"].get("descendants"))
                leaks = [i for i, report in enumerate(batch)
                         if resource_ids(report["diagnostics"]["lifecycle_before"]) !=
                            resource_ids(report["diagnostics"]["lifecycle_after"])]
                file = f"batch-{count}cpu-r{repetition}-{arm}.json"
                save(args.out / file, batch)
                matrix.append({"cpu_count": count, "cpuset": affinity, "repeat": repetition,
                               "arm": arm, "public_calls": len(batch), "lifecycle_deltas": leaks,
                               "observations": file})
            os.sched_setaffinity(0, available)
    save(args.out / "cpu-lifecycle.json", matrix)
    # Mandatory original/minimized error-before-first-byte reproducer.
    control = cases[0]
    assert control["options"]["serialize_then_panic"]
    minimized = json.loads(json.dumps(control))
    minimized["input"] = {"value": None}  # custom Serialize value is fixed, independent of this field
    control_results = []
    for variant, case in (("original", control), ("minimized", minimized)):
        item = contract.evaluate_case(case, surface_for(case), precondition,
            lambda current, arm, index: observe(binaries[arm], current, builds[arm]),
            builds, compare_hook=compare, repetitions=2)
        control_results.append({"variant": variant, "case": case, "result": item})
    save(args.out / "mandatory-control.json", control_results)
    summary = {
        "format": "optimbench-fuzz-v1", "task": TASK, "timing_performed": False,
        "scope": "8 documented public writer/reader/stream entrypoints; incomplete full surface",
        "seeds": [1729,2718,31415], "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
        "cases": len(cases), "fresh_observer_processes_per_arm_per_case": 2,
        "finding_cases": len(results),
        "mandatory_original_and_minimized_reproduced": all(
            any(f["kind"] == "contract_regression" for f in item["result"].get("findings", []))
            for item in control_results),
        "cpu_lifecycle_matrix": {"cpu_counts":[1,2,8], "repetitions":5,
                                 "same_process_public_calls_per_batch":32,
                                 "lifecycle_deltas":sum(len(row.get("lifecycle_deltas", [])) for row in matrix),
                                 "child_inventory": "proc-stat PPID/starttime fallback; availability retained per snapshot"},
        "peak_memory": {"available":False,"mode":"report-only","verdict_impact":False},
        "qualification": "undertested",
        "missing": ["corrected full surface authority", "full public corpus mutation",
                    "time-boxed coverage-guided campaign", "changed-line/branch coverage",
                    ">=20 compiled non-equivalent mutants", "candidate-bound sanitizer/race builds",
                    "non-default feature matrix", "allocation fault injection"],
    }
    save(args.out / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
