#!/usr/bin/env python3
"""Atheris one-arm discovery; collected cases require separate both-arm replay."""
import hashlib
import json
import os
from pathlib import Path
import sys

from adapter import DIALECT_PAIRS, ROOT, SURFACES, digest, make_case, precondition


def decode(data):
    head = (data[:4] + b"\0\0\0\0")[:4]
    symbol = SURFACES[head[0] % len(SURFACES)]
    read, write = DIALECT_PAIRS[head[1] % len(DIALECT_PAIRS)]
    text = data[4:4100].decode("utf-8", errors="replace")
    if head[2] % 3 == 0:
        text = "SELECT " + str(int.from_bytes(data[4:12], "little", signed=True)) + " AS a"
    elif head[2] % 3 == 1:
        text = "SELECT " + text
    case = make_case(symbol, text, read=read, write=write, index=head[3])
    case["generator"] = "sqlglot-atheris-typed-decode-v1"
    case["sequence"] = ["invoke"]
    return case


def main():
    # Environment is supplied by the trusted shared runner, not the candidate.
    runtime = Path(os.environ["SQLGLOT_FUZZ_RUNTIME"]).resolve()
    output = Path(os.environ["SQLGLOT_FUZZ_CORPUS_OUT"])
    build_sha256 = os.environ["SQLGLOT_FUZZ_BUILD_SHA256"]
    if len(build_sha256) != 64 or any(c not in "0123456789abcdef" for c in build_sha256):
        raise ValueError("Authenticated build SHA-256 required")
    output.mkdir(parents=True, exist_ok=False)
    surfaces = {x["id"]: x for x in json.loads((ROOT / "surface.json").read_text())["surfaces"]}
    sys.path.insert(0, str(runtime))
    import atheris
    with atheris.instrument_imports():
        import sqlglot
        from driver import observe
    if not Path(sqlglot.__file__).resolve().is_relative_to(runtime):
        raise RuntimeError("Wrong protected runtime loaded")
    counter = 0
    with (output / "order.jsonl").open("w") as order:
        def fuzz(data):
            nonlocal counter
            case = decode(data)
            if not precondition(case, surfaces[case["surface_id"]])["valid"]:
                raise RuntimeError("Typed decoder generated an inadmissible case")
            case_sha = digest(case)
            (output / (case_sha + ".case.json")).write_text(json.dumps(case, sort_keys=True) + "\n")
            result = observe(case, sqlglot, build_sha256)
            result["diagnostics"]["memory"]["scope"] = "coverage-worker-cumulative-not-per-unit"
            receipt = output / f"{counter:09d}-{case_sha}.observation.json"
            receipt.write_text(json.dumps(result, sort_keys=True) + "\n")
            order.write(json.dumps({
                "index": counter, "input_sha256": hashlib.sha256(data).hexdigest(),
                "case_sha256": case_sha, "observation": receipt.name,
                "observation_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }) + "\n")
            order.flush()
            counter += 1

        atheris.Setup(sys.argv, fuzz)
        atheris.Fuzz()


if __name__ == "__main__":
    main()
