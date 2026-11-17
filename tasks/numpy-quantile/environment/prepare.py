#!/usr/bin/env python3
"""Image-build input authentication and exact original public commitment gate."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, "/app/workload")
from runner import locked_cases

workload = Path("/app/workload")
cases = locked_cases(workload / "inputs")
if len(sys.argv) > 1:
    build = Path(sys.argv[1])
    result = subprocess.run([str(build / "run")], check=True, capture_output=True, text=True)
    got = {}
    for line in result.stdout.splitlines():
        name, size, sha = line.split("\t")
        if name in got:
            raise ValueError("Duplicate output")
        got[name] = (int(size), sha)
    expected = {r["id"]: (r["bytes"], r["sha256"]) for r in
                json.loads((workload / "baseline-outputs.json").read_text())}
    if got != expected:
        raise ValueError("Pinned complete-output baseline mismatch")
    print(f"Authenticated all {len(got)} original public commitments")
