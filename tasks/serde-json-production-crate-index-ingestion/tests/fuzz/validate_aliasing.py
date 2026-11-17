#!/usr/bin/env python3
"""Run pristine and real broken-clone controls in the pinned task image."""
import argparse
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

from adapter import generate_aliases, precondition, compare
from run import compile_probe, observe
from validate import surface_for
from judge import fuzzing_contract as contract
from judge.verifier import build_inventory

EXTERNAL_COMMAND_CONTEXTS = {"main": "pinned-worker-verifier-observer"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=Path("/app/baseline-build"))
    parser.add_argument("--source", type=Path, default=Path("/app/serde-json"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.out / "broken-clone-source"
    shutil.copytree(args.source, source)
    path = source / "src/value/mod.rs"
    text = path.read_text()
    marker = "#[derive(Clone, Eq, PartialEq, Hash)]"
    anchor = "impl Debug for Value {"
    if text.count(marker) != 1 or text.count(anchor) != 1:
        raise ValueError("Pinned Value clone declaration changed")
    mutant = """impl Clone for Value {
    fn clone(&self) -> Self {
        match self {
            Value::Null => Value::Null,
            Value::Bool(v) => Value::Bool(*v),
            Value::Number(v) => Value::Number(v.clone()),
            Value::String(v) => Value::String(v.clone()),
            Value::Array(v) => Value::Array(v.clone()),
            Value::Object(_) => Value::Null,
        }
    }
}
"""
    path.write_text(text.replace(marker, marker.replace("Clone, ", ""), 1)
                    .replace(anchor, mutant + anchor, 1))
    candidate = args.out / "broken-clone-build"
    built = subprocess.run(["sh", "/app/workload/build.sh", str(candidate)],
                           env={**os.environ, "OPTIMBENCH_SOURCE": str(source)},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    (args.out / "build.stdout").write_bytes(built.stdout)
    (args.out / "build.stderr").write_bytes(built.stderr)
    built.check_returncode()
    builds = {"baseline": args.baseline, "candidate": candidate}
    identities = {arm: build_inventory(folder) for arm, folder in builds.items()}
    binaries, compiled = {}, {}
    for arm, folder in builds.items():
        binaries[arm] = args.out / (arm + "-probe")
        compiled[arm] = compile_probe(folder, binaries[arm])
    results = []
    for case in generate_aliases(random.Random(2718)):
        positive = contract.evaluate_case(
            case, surface_for(case), precondition,
            lambda value, arm, index: observe(binaries["baseline"], value, identities["baseline"]),
            {arm: identities["baseline"] for arm in builds}, compare_hook=compare, repetitions=2)
        negative = contract.evaluate_case(
            case, surface_for(case), precondition,
            lambda value, arm, index: observe(binaries[arm], value, identities[arm]),
            identities, compare_hook=compare, repetitions=2)
        results.append({"case": case, "positive": positive, "negative": negative})
    passed = all(not row["positive"].get("findings") and any(
        finding["kind"] == "contract_regression" and finding.get("reproduced")
        for finding in row["negative"].get("findings", [])) for row in results)
    report = {"format": "serde-json-native-aliasing-control-v1", "passed": passed,
              "timing_performed": False, "candidate_verdict": None, "is_control": True,
              "mutation": "Value::Object clone returns Null in a temporary source copy",
              "builds": identities, "compiled": compiled, "cases": results}
    (args.out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": passed, "cases": len(results), "timing_performed": False}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
