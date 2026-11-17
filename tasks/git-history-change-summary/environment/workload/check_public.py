#!/usr/bin/env python3
import json
from pathlib import Path
import sys

rows = json.loads((Path(__file__).parent / "baseline-outputs.json").read_text())
expected = {row["id"]: (row["expect_len"], row["expect_sha"]) for row in rows}
actual = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    name, size, digest = line.split("\t")
    if name in actual:
        raise ValueError("Duplicate case")
    actual[name] = int(size), digest
assert actual == expected
