#!/usr/bin/env python3
"""Public aliasing facts in isolated workers; no task-owned verdict logic."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

from adapter import generate, seal

HERE = Path(__file__).resolve().parent


def cases():
    selected = []
    for case in generate(random.Random(1729), {"cases": 12}):
        if case["operation"] not in ("block_config_state", "frozen_match_result"):
            continue
        case["input"] = {"sql": "SELECT 1;", "domain": "success"}
        case["options"] = {"dialect": "ansi", "processes": 1}
        selected.append(seal(case))
    return selected


def surface_for(case):
    return {
        "id": case["surface_id"], "visibility": "public",
        "contract_id": case["contract_id"], "precondition_id": case["precondition_id"],
        "oracle": {"baseline_terminations": ["return"], "baseline_exit_codes": [0],
                   "required_error_fields": {"class": "nonempty_string", "message": "string"}},
        "contract_basis": (
            "Documented mutable BlockConfig.incorporate; unchanged ReflowConfig getter "
            "must not retain mutations to a prior returned configuration."
            if case["operation"] == "block_config_state" else
            "Frozen MatchResult empty_at/append value semantics; identity-only interning "
            "is allowed. No nested dictionary mutation or identity assertion."
        ),
    }


def observe(runtime, case, build_sha, out):
    with tempfile.TemporaryDirectory(prefix="sqlfluff-rc-call-") as temporary:
        root = Path(temporary)
        case_path = root / "case.json"
        case_path.write_text(json.dumps(case))
        with (root / "report.json").open("w+b") as report:
            fd = report.fileno()
            result = subprocess.run([
                sys.executable, str(HERE / "driver.py"), "--runtime", str(runtime),
                "--case", str(case_path), "--build-sha256", build_sha,
                "--report-fd", str(fd),
            ], pass_fds=(fd,), capture_output=True, timeout=30)
            out.with_suffix(".stdout").write_bytes(result.stdout)
            out.with_suffix(".stderr").write_bytes(result.stderr)
            report.seek(0)
            payload = report.read()
        out.with_suffix(".observation.json").write_bytes(payload)
        if result.returncode or not payload:
            raise ValueError("RC arm transport failed; inspect retained separate streams")
        return json.loads(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("baseline-sha", "candidate-sha"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    rows = []
    for case in cases():
        row = {"case": case, "surface": surface_for(case)}
        for arm in builds:
            runtime = getattr(args, arm) / "runtime"
            row[arm] = [
                observe(runtime, case, builds[arm],
                        args.out / f"{case['operation']}-{arm}-{index}")
                for index in range(2)
            ]
        rows.append(row)
    data = {
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": rows,
        "worker_protocol": "two fresh subprocesses per arm/case within shared hardened DockerExecutor",
        "same_process_state": "each BlockConfig operation contains three documented mutate/get sequences",
        "source_files": {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ("adapter.py", "driver.py", "rc_driver.py", "rc_hooks.py")
        },
        "full_fuzz_qualification": False,
    }
    (args.out / "aliasing-facts.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
