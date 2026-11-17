#!/usr/bin/env python3
"""Independent JSON transport for jq CLI observations; stdout/stderr stay bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from adapter import TASK, content_hash, encode, decode, precondition


def observe(binary, case, build_sha256, timeout=30):
    valid = precondition(case, {})
    if not valid["valid"]:
        raise ValueError(valid["reason"])
    start = time.monotonic()
    env = dict(os.environ, LC_ALL="C.UTF-8", TZ="UTC")
    command = [str(binary), "-c", *case["options"]["argv"], case["options"]["program"]]
    try:
        result = subprocess.run(command, input=decode(case["input"]["data"]),
                                capture_output=True, timeout=timeout, env=env)
        termination = {"kind": "return" if result.returncode >= 0 else "signal",
                       "exit_code": result.returncode}
        stdout, stderr = result.stdout, result.stderr
        outcome = {"kind": "ok" if result.returncode == 0 else "error",
                   "category": "cli-exit", "value": result.returncode}
    except subprocess.TimeoutExpired as error:
        termination = {"kind": "timeout", "exit_code": None}
        stdout, stderr = error.stdout or b"", error.stderr or b""
        outcome = {"kind": "error", "category": "deadline", "value": None}
    return {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {"task": TASK, "surface_id": case["surface_id"],
                     "case_sha256": content_hash(case), "build_sha256": build_sha256},
        "termination": termination, "outcome": outcome,
        "effects": {"stdout": {"encoding": "base64", "data": encode(stdout)},
                    "stderr": {"encoding": "base64", "data": encode(stderr)}},
        "diagnostics": {"elapsed_seconds": time.monotonic() - start,
                        "argv": command, "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                        "stderr_sha256": hashlib.sha256(stderr).hexdigest()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--build-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(observe(args.binary, json.loads(args.case.read_text()),
                             args.build_sha256), sort_keys=True))
