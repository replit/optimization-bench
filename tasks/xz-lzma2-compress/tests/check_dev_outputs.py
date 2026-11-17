#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import sys

task = Path(__file__).resolve().parent.parent
manifest = json.loads((task / "environment/workload/inputs/manifest.json").read_text())
output = Path(sys.argv[1]).resolve(strict=True)
expected = set()
for case in manifest["cases"]:
    target = output / (case["id"] + ".out")
    expected.add(target.name)
    data = target.read_bytes()
    if len(data) != case["output_bytes"] or hashlib.sha256(data).hexdigest() != case["output_sha256"]:
        raise SystemExit("Output mismatch: " + case["id"])
if {p.name for p in output.iterdir()} != expected:
    raise SystemExit("Unexpected output-directory contents")
print("All development outputs match the pinned baseline.")
