#!/usr/bin/env python3
"""Assemble raw fresh/persistent public API observations, never verdicts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from rc_api_cases import SCHEDULE, repeat_cases, stream_cases, surface

HERE = Path(__file__).resolve().parent


def worker(runtime, cases, build_sha, prefix):
    with tempfile.TemporaryDirectory(prefix="sqlfluff-public-request-") as directory:
        root = Path(directory)
        request = root / "request.json"
        request.write_text(json.dumps(cases))
        with (root / "private-report.json").open("w+b") as report:
            fd = report.fileno()
            result = subprocess.run([
                sys.executable, str(HERE / "rc_api_worker.py"), "--runtime", str(runtime),
                "--request", str(request), "--build-sha256", build_sha,
                "--report-fd", str(fd),
            ], pass_fds=(fd,), capture_output=True, timeout=180,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                     "PYTHONWARNINGS": "ignore::UserWarning:multiprocessing.resource_tracker"})
            report.seek(0)
            payload = report.read()
        Path(str(prefix) + ".stdout").write_bytes(result.stdout)
        Path(str(prefix) + ".stderr").write_bytes(result.stderr)
        Path(str(prefix) + ".report.json").write_bytes(payload)
        if result.returncode or not payload:
            raise ValueError("public API worker transport failed; retained streams are infrastructure evidence")
        data = json.loads(payload)
        if len(data.get("observations", [])) != len(cases):
            raise ValueError("incomplete public API request")
        return data


def lifecycle(observation):
    raw = observation["diagnostics"]["lifecycle"]
    def point(name):
        value = raw[name]
        return {**value, **value["counts"]}
    return {"available": all(raw[name].get("available") for name in ("before", "after")),
            "before": point("before"), "after": point("after")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("baseline-sha", "candidate-sha"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    repeats, faults = repeat_cases(), stream_cases()
    fresh, persistent, workers, fault_observations = {}, {}, {}, {}
    for arm, sha in builds.items():
        runtime = getattr(args, arm) / "runtime"
        fresh[arm] = [[worker(runtime, [case], sha, args.out / f"{arm}-fresh-{index}-{replay}")
                       ["observations"][0] for replay in range(2)]
                      for index, case in enumerate(repeats)]
        batch = worker(runtime, [repeats[index] for index in SCHEDULE], sha,
                       args.out / f"{arm}-persistent")
        persistent[arm], workers[arm] = batch["observations"], batch["worker"]
        fault_observations[arm] = [
            [worker(runtime, [case], sha, args.out / f"{arm}-file-{index}-{replay}")
             ["observations"][0] for replay in range(2)]
            for index, case in enumerate(faults)]
    repeat_rows, per_call = [], []
    for index, case in enumerate(repeats):
        positions = [position for position, value in enumerate(SCHEDULE) if value == index]
        repeat_rows.append({
            "case": case, "surface": surface(case),
            **{arm: [persistent[arm][pos] for pos in positions] for arm in builds},
            **{"fresh_" + arm: fresh[arm][index] for arm in builds},
        })
    for position, index in enumerate(SCHEDULE):
        per_call.append({"case_id": repeats[index]["case_sha256"], "sequence_position": position,
                         **{arm: lifecycle(persistent[arm][position]) for arm in builds}})
    fault_rows = [
        {"case": case, "surface": surface(case),
         **{arm: fault_observations[arm][index] for arm in builds}}
        for index, case in enumerate(faults)]
    save = lambda name, value: (args.out / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    save("repeat-facts.json", {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": repeat_rows, "workers": workers, "lifecycle": per_call,
        "schedule": list(SCHEDULE), "full_surface_coverage_claim": False,
    })
    save("stream-facts.json", {
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": fault_rows,
        "scope": "documented file reads and persistence; no arbitrary writer callback is asserted",
        "full_surface_coverage_claim": False,
    })


if __name__ == "__main__":
    main()
