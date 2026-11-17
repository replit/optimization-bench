#!/usr/bin/env python3
"""Authenticate all task outputs and exercise source-rebuilt alternate controls."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile

TASK = Path(__file__).resolve().parents[2]
ROOT = TASK.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools/runner")]
from judge import heldout
from judge.verifier import load_task, case_roles, performance_units
from runner import Context, run_case
from docker_executor import DockerExecutor

OPERATIONS = ("group-gharchive", "csv-gharchive")


def execute(module, case, inputs, workload, build, image):
    with tempfile.TemporaryDirectory(prefix="jq-event-check-") as temporary:
        ctx = Context(Path(temporary), build, inputs, Path(temporary), workload, docker=True)
        executor = DockerExecutor(image, cpuset="0-7")
        outputs, results = run_case(module, case, ctx, executor)
        if any(result.leftovers for result in results):
            raise ValueError("Process left behind")
        expected = {doc["path"]: (doc["expect_len"], doc["expect_sha"])
                    for doc in case.data["docs"]}
        actual = {key: (len(value), hashlib.sha256(value).hexdigest())
                  for key, value in outputs.items()}
        return {"correct": actual == expected, "seconds": executor.case_time_ns / 1e9}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"mode": "ordinary baseline and source controls, not A/A", "units": []}
    with heldout.materialized_task(TASK) as task:
        module = load_task(task)
        settings = json.loads((task / "tests/judge.json").read_text())
        for public in (True, False):
            split = "public" if public else "private"
            inputs = task / ("environment/workload/inputs" if public else "tests/heldout")
            cases = module.cases(inputs)
            roles = case_roles(settings, {case.id: case for case in cases}, public)
            assert all(role == "performance" for role in roles.values())
            units = performance_units(settings, roles, public)
            if public:
                assert units is None
            else:
                assert units == {case.id: [case.id] for case in cases}
            assert len(cases) == 4
            for case in cases:
                operation = case.data["docs"][0].get("operation", "strip-descriptions")
                baseline = [execute(module, case, inputs, task / "environment/workload",
                                    args.build, args.image) for _ in range(2)]
                assert all(row["correct"] for row in baseline), "Baseline output mismatch"
                row = {"operation": operation, "split": split,
                       "baseline_seconds": [r["seconds"] for r in baseline],
                       "complete_outputs_match": True}
                if operation in OPERATIONS:
                    assert min(row["baseline_seconds"]) >= 1.3, "New unit is below 1.3 seconds"
                    controls = {}
                    for control in ("wrong-result", "omit-work", "missing-output", "slow-correct"):
                        result = execute(module, case, inputs, task / "environment/workload",
                                         args.controls / control, args.image)
                        assert result["correct"] == (control == "slow-correct"), "Control failed"
                        controls[control] = result
                    # This is a diagnostic timing check, not a new A/A run.
                    assert controls["slow-correct"]["seconds"] > statistics.median(
                        row["baseline_seconds"]), "Correct-but-slow delay was not visible"
                    row["source_controls"] = controls
                report["units"].append(row)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
