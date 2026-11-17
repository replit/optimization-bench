#!/usr/bin/env python3
"""Compile against an admitted build, run one case; no task source modifications."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

from adapter import TASK, case_sha256, encode

EXTERNAL_COMMAND_CONTEXTS = {
    "compile_probe": "pinned-worker-verifier-observer",
    "observe": "pinned-worker-verifier-observer",
    "observe_batch": "pinned-worker-verifier-observer",
}


def compile_probe(build, output):
    deps = build / "target/release/deps"
    command = ["/usr/local/cargo/bin/rustc", "--edition=2021", "-O",
               "-L", "dependency=" + str(deps)]
    libraries = {}
    for name in ("serde", "serde_json"):
        paths = list(deps.glob("lib" + name + "-*.rlib"))
        if len(paths) != 1:
            raise ValueError("missing/ambiguous admitted " + name + " library")
        libraries[name] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
        command += ["--extern", name + "=" + str(paths[0])]
    command += [str(Path(__file__).with_name("probe.rs")), "-o", str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=120,
                   env={**os.environ, "RUSTUP_HOME": "/usr/local/rustup",
                        "RUSTUP_TOOLCHAIN": "1.93.1"})
    return {"binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "libraries": libraries, "command": command}


def envelope(report, case, build_sha256, raw):
    report["format"] = "optimbench-fuzz-observation-v1"
    report["identity"] = {"task": TASK, "surface_id": case["surface_id"],
                          "case_sha256": case_sha256(case), "build_sha256": build_sha256}
    report["diagnostics"]["independent_input_tape_sha256"] = hashlib.sha256(raw).hexdigest()
    return report


def observe(binary, case, build_sha256, timeout=30):
    raw = encode(case)
    try:
        process = subprocess.run([str(binary)], input=raw, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        report = {"termination": {"kind": "timeout", "exit_code": None},
                  "outcome": {"kind": "error", "category": "timeout",
                              "io_kind": None, "line": None, "column": None},
                  "effects": {}, "diagnostics": {}}
    else:
        if process.returncode < 0:
            report = {"termination": {"kind": "signal", "exit_code": process.returncode,
                                      "signal": -process.returncode},
                      "outcome": {"kind": "error", "category": "signal",
                                  "io_kind": None, "line": None, "column": None},
                      "effects": {}, "diagnostics": {}}
        elif process.returncode != 0:
            # A verifier tape/loader failure is infrastructure, not equivalent output.
            raise RuntimeError("probe infrastructure exit " + str(process.returncode))
        else:
            report = json.loads(process.stdout)
        report["diagnostics"]["stderr_hex"] = process.stderr.hex()
    return envelope(report, case, build_sha256, raw)


def observe_batch(binary, cases, build_sha256, timeout=120):
    """Deliberate same-process sequence, distinct from fresh-worker replay."""
    frames = [encode(case) for case in cases]
    process = subprocess.run([str(binary), "--batch"], input=b"".join(
        struct.pack("<I", len(frame)) + frame for frame in frames),
        capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError("batch infrastructure/abnormal termination; retain raw exit separately: " +
                           str(process.returncode))
    reports = [json.loads(line) for line in process.stdout.splitlines()]
    if len(reports) != len(cases):
        raise ValueError("batch observation count mismatch")
    return [envelope(report, case, build_sha256, raw)
            for report, case, raw in zip(reports, cases, frames)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    receipt = compile_probe(args.build, args.binary)
    case = json.loads(args.case.read_text())
    print(json.dumps(observe(args.binary, case, receipt["libraries"]["serde_json"]), sort_keys=True))


if __name__ == "__main__":
    main()
