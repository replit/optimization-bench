#!/usr/bin/env python3
"""Narrow pristine calibration, not an adequacy/candidate campaign."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import random

from adapter import generate, precondition
from cli_driver import observe


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def public_selection():
    root = Path("/app/workload/inputs")
    manifest = json.loads((root/"manifest.json").read_text())
    if manifest["format"] != "optimbench-inputs-v1":
        raise ValueError("Unrecognized protected public manifest")
    records = []
    for batch in manifest["splits"]["dev"]:
        for document in batch["docs"]:
            path = root/"files"/document["sha256"]
            raw = path.read_bytes()
            if len(raw) != document["bytes"] or sha(raw) != document["sha256"]:
                raise ValueError("Frozen public document bytes differ")
            records.append({
                "split": "public_dev", "data_b64": base64.b64encode(raw).decode(),
                "sha256": document["sha256"],
                "provenance": "protected-public-manifest:dev:"+document["id"],
            })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("/app/baseline-build/project/jq"))
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build = sha(args.binary.read_bytes())
    selected = public_selection() if args.public else []
    rows = []
    for case in generate(random.Random(1729), {"cases": 64, "public_cases": selected}):
        assert precondition(case, {})["valid"]
        observation = observe(args.binary, case, build)
        if observation["termination"]["kind"] != "return":
            raise RuntimeError("Baseline calibration crashed or timed out")
        rows.append({
            "case_sha256": case["case_sha256"], "surface_id": case["surface_id"],
            "generator": case["generator"], "public_provenance": case.get("public_provenance"),
            "termination": observation["termination"], "outcome": observation["outcome"],
            "stdout_sha256": observation["diagnostics"]["stdout_sha256"],
            "stderr_sha256": observation["diagnostics"]["stderr_sha256"],
        })
    result = {
        "format": "jq-pristine-calibration-v1", "scope": "CLI slice; no candidate verdict/adequacy claim",
        "seed": 1729, "pristine_jq_binary_sha256": build,
        "public_originals_selected": len(selected),
        "cases": len(rows), "rows": rows,
        "expected_error_returns_are_valid_observations": True,
        "memory": {"availability": "not-measured", "verdict_impact": False},
        "changed_coverage": "not-run", "sanitizers": "not-run", "mutation": "not-run",
    }
    args.out.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
