#!/usr/bin/env python3
"""Verifier-only bounded probe compile/run against the exact admitted rlib."""
import json
import os
from pathlib import Path
import subprocess
import sys

EXTERNAL_COMMAND_CONTEXTS = {"<module>": "pinned-worker-verifier-observer"}

folder = Path("/verifier-differential")
libs = list(Path("/app/build/target/release/deps").glob("libserde_json-*.rlib"))
if len(libs)!=1:
    raise ValueError("ambiguous admitted serde_json library")
build = subprocess.run(["/usr/local/cargo/bin/rustc","--edition=2021","-O",
    "-L","dependency=/app/build/target/release/deps",
    "--extern","serde_json="+str(libs[0]),str(folder/"probe.rs"),
    "-o","/scratch/api-probe"],capture_output=True,
    env={**os.environ, "RUSTUP_HOME":"/usr/local/rustup", "RUSTUP_TOOLCHAIN":"1.93.1"})
if build.returncode:
    raise ValueError("verifier probe failed to compile: "+build.stderr.decode(errors="replace"))
case = json.loads(Path(sys.argv[1]).read_text())
run = subprocess.run(["/scratch/api-probe"],input=json.dumps(case).encode(),
                     capture_output=True,timeout=10)
if run.returncode or run.stderr:
    raise ValueError("verifier API probe failed: "+run.stderr.decode(errors="replace"))
result = {"api":json.loads(run.stdout)}
if "writer_fault" in case:
    serde = list(Path("/app/build/target/release/deps").glob("libserde-*.rlib"))
    if len(serde) != 1:
        raise ValueError("ambiguous admitted serde trait library")
    writer_build = subprocess.run(["/usr/local/cargo/bin/rustc", "--edition=2021", "-O",
        "-L", "dependency=/app/build/target/release/deps",
        "--extern", "serde_json=" + str(libs[0]), "--extern", "serde=" + str(serde[0]),
        str(folder / "writer_probe.rs"), "-o", "/scratch/writer-probe"],
        capture_output=True, env={**os.environ, "RUSTUP_HOME":"/usr/local/rustup",
                                  "RUSTUP_TOOLCHAIN":"1.93.1"})
    if writer_build.returncode:
        raise ValueError("verifier writer probe failed to compile: " +
                         writer_build.stderr.decode(errors="replace"))
    writer = subprocess.run(["/scratch/writer-probe"],
        input=json.dumps(case["writer_fault"]).encode(), capture_output=True, timeout=10)
    # Aborts/signals are candidate observations, not infrastructure exceptions.
    result["writer"] = {"exit_code":writer.returncode,
        "stderr_class":"nonempty" if writer.stderr else "empty",
        "output":json.loads(writer.stdout) if writer.returncode == 0 else None}
if "registry_hex" in case:
    path = Path("/scratch/registry.jsonl")
    path.write_bytes(bytes.fromhex(case["registry_hex"]))
    registry = subprocess.run(["/app/build/prog",str(path)],capture_output=True,timeout=10)
    result["registry"] = {"exit_code":registry.returncode,
        "stdout":json.loads(registry.stdout) if registry.returncode==0 else registry.stdout.decode(errors="replace"),
        "stderr":registry.stderr.decode(errors="replace")}
print(json.dumps(result,sort_keys=True,ensure_ascii=True))
