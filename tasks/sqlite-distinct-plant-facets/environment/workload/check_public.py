"""Exact full-output build gate; no benchmark result or timing is produced."""
import json
from pathlib import Path
import subprocess
import sys

workload = Path(__file__).resolve().parent
manifest = json.loads((workload / "inputs/manifest.json").read_text())
expected = {doc["path"]: (doc["expect_len"], doc["expect_sha"])
            for batch in manifest["splits"]["public"] for doc in batch["docs"]}
run = subprocess.run([str(Path(sys.argv[1]) / "run")],
                     capture_output=True, text=True, check=True)
got = {}
for line in run.stdout.splitlines():
    name, length, digest = line.split("\t")
    if name in got:
        raise ValueError("Duplicate public output")
    got[name] = (int(length), digest)
assert got == expected, "Complete public output commitments differ"
print("Authenticated complete distinct-input public outputs")
