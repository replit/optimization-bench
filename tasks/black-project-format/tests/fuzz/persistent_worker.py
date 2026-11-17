#!/usr/bin/env python3
"""Bounded verifier-owned worker: several public cases, one imported native arm."""
import argparse
import hashlib
import json
from pathlib import Path

from worker import load_monitor, observe


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--monitor", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    if not isinstance(cases, list) or not 2 <= len(cases) <= 16:
        parser.error("Persistent batch requires 2..16 bounded cases")
    monitor = load_monitor(args.monitor)
    # observe imports black once; Python's module cache keeps this same native
    # target in this process for every subsequent public case.
    observations = [observe(case, args.runtime, args.build_sha256, monitor) for case in cases]
    print(json.dumps({
        "format": "black-persistent-worker-observations-v1",
        "observations": observations, "monitor_sha256": hashlib.sha256(args.monitor.read_bytes()).hexdigest(),
        "target_library_in_process": True,
        "calls": sum(len(case["sequence"]) for case in cases),
    }, ensure_ascii=False, sort_keys=True))
