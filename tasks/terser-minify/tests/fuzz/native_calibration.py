#!/usr/bin/env python3
"""Untimed local native calibration; never emits a qualification/score receipt."""
import argparse
from functools import partial
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools/runner")]
from judge import cpu_correctness, fuzzing_generic, fuzzing_io
from judge.verifier import build_inventory

TASK = ROOT / "tasks/terser-minify"
GATES = {"repeat_in_process": "repeat-facts.json", "aliasing": "alias-facts.json",
         "stream_faults": "stream-facts.json"}


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cli", action="store_true", help="Requires root + mount namespace support")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    def verifier_files():
        return {str(p.relative_to(TASK)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((TASK / "tests/fuzz").iterdir())
                if p.is_file() and p.suffix in (".py", ".mjs", ".cjs", ".json")}
    verifier_before = verifier_files()
    _, hooks = fuzzing_generic.task_runners(TASK)
    driver = importlib.import_module(hooks.__package__ + ".rc_driver")
    native_cli = importlib.import_module(hooks.__package__ + ".native_cli")
    original_mask = sorted(os.sched_getaffinity(0))
    if len(original_mask) < 8:
        raise ValueError("Eight actual allowed CPUs required; never invent/substitute CPU evidence")
    source_sha = build_inventory(args.baseline)
    summary = {"format": "terser-local-native-calibration-v1", "timing_performed": False,
               "aa_sampling_performed": False, "claimable": False,
               "baseline_build_sha256": source_sha,
               "original_cpu_mask": original_mask, "positive": [], "controls": [],
               "node_version": subprocess.check_output([str(args.node), "--version"], text=True).strip(),
               "node_sha256": hashlib.sha256(args.node.read_bytes()).hexdigest(),
               "scope": "Authenticated upstream library/CLI only; not pinned-image, Docker or GCP qualification"}

    def library(candidate, out):
        builds = {"baseline": source_sha, "candidate": build_inventory(candidate)}
        facts = driver.collect(args.baseline, candidate, builds, out, str(args.node))
        context = {"builds": builds, "task": TASK, "hooks": hooks,
                   "cpu_requested": sorted(os.sched_getaffinity(0)),
                   "cpu_limits": cpu_correctness.DEFAULT_LIMITS}
        results = {}
        for gate, filename in GATES.items():
            result = fuzzing_generic._call_evidence(gate, facts[filename], hooks, context)
            # Validate actual in-process masks and independently bound observations.
            runs, repros = cpu_correctness._normalize(gate, result, context)
            results[gate] = {"status": result["status"], "physical_observations": len(runs),
                             "reproductions": len(repros),
                             "findings": result["evidence"]["findings"]}
            save(out / (gate + "-verdict.json"), result)
        return results

    def cli(candidate, out):
        context = {"task": TASK, "hooks": hooks, "out": out, "baseline": args.baseline,
                   "candidate": candidate, "image": "local-native-calibration-not-an-image",
                   "cpuset": ",".join(map(str, sorted(os.sched_getaffinity(0)))),
                   "builds": {"baseline": source_sha, "candidate": build_inventory(candidate)},
                   "sources": {"baseline": source_sha, "candidate": build_inventory(candidate)},
                   "public_inputs": None, "projects": None}
        out.mkdir()
        # Replace only container transport with real local namespace execution.
        # No observation, error, process identity, comparison or verdict is mocked.
        with patch.object(native_cli.cli_faults, "run_pair", partial(native_cli.run_pair, node=args.node)):
            result = fuzzing_io.cli_gate(context)
        save(out / "verdict.json", result)
        return {"status": result["status"],
                "limitations": result.get("evidence", {}).get("baseline_limitations")}

    try:
        for count in (1, 2, 4, 8):
            cpus = original_mask[:count]
            os.sched_setaffinity(0, cpus)
            output = args.out / f"cpu-{count}"
            results = library(args.baseline, output)
            if not all(item["status"] == "passed" for item in results.values()):
                raise ValueError("Positive library calibration did not pass")
            row = {"cpus": cpus, "library": results}
            if args.cli:
                row["cli"] = cli(args.baseline, args.out / f"cli-cpu-{count}")
                if row["cli"]["status"] != "passed":
                    raise ValueError("Positive CLI calibration did not pass")
            summary["positive"].append(row)
            save(args.out / "summary.json", summary)
        os.sched_setaffinity(0, original_mask[:2])
        for kind, target in (("semantic", "repeat_in_process"),
                             ("state-counter", "repeat_in_process"),
                             ("alias-cache", "aliasing"),
                             ("callback-swallow", "stream_faults"),
                             ("fd-leak", "repeat_in_process")):
            build = args.out / ("control-build-" + kind)
            shutil.copytree(args.baseline, build, symlinks=True)
            mutation = subprocess.run([str(args.node), str(TASK / "tests/fuzz/control.mjs"),
                                       str(build / "project"), kind], capture_output=True, check=True)
            save(args.out / (kind + "-mutation.json"), json.loads(mutation.stderr))
            environment = dict(os.environ, PATH=str(args.node.parent) + ":" + os.environ.get("PATH", ""))
            run = subprocess.run(["npm", "run", "build"], cwd=build / "project", env=environment,
                                 capture_output=True, check=True)
            (args.out / (kind + "-build.log")).write_bytes(run.stdout + run.stderr)
            result = library(build, args.out / ("control-" + kind))
            if result[target]["status"] not in ("contract_regression", "nondeterministic", "needs_review"):
                raise ValueError("Meaningful native control was not rejected: " + kind)
            summary["controls"].append({"kind": kind, "target": target, "library": result,
                                         "candidate_build_sha256": build_inventory(build)})
            save(args.out / "summary.json", summary)
        if args.cli:
            # Semantic control also modifies the real CLI bundle, not observer data.
            summary["cli_negative"] = cli(args.out / "control-build-semantic", args.out / "cli-control")
            if summary["cli_negative"]["status"] != "contract_regression":
                raise ValueError("Real upstream CLI semantic control was not rejected")
        if verifier_files() != verifier_before:
            raise ValueError("Verifier source changed during calibration")
        summary["verifier_files_sha256"] = verifier_before
        save(args.out / "summary.json", summary)
    finally:
        os.sched_setaffinity(0, original_mask)


if __name__ == "__main__":
    main()
