#!/usr/bin/env python3
"""Derive the public TPC-H/TPC-DS development inputs from pinned SQLGlot fixtures.

TPC query text is not stored in this repository or in the task image. The public
preflight first fetches and authenticates the three SQLGlot files declared in
payload.json under workload/inputs/upstream/. The protected preparation step in
public-input-packet.json then runs this standard-library script offline with the
task environment as ENVIRONMENT. It reproduces the frozen optimizer, lineage and
structural-diff requests exactly as public/optimizer/prepare.py and the reviewed
alternates packets did, and writes only outputs whose size and SHA-256 equal the
packet commitments and the public manifest. Existing bytes are never replaced.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys

UPSTREAM = "workload/inputs/upstream"
OUTPUT = "workload/inputs/files"
SOURCES = {
    "tests/helpers.py": (19719, "3e60e402106039cd121968b44ace160cb05099c7faf3e616ddbd639a47cbb615"),
    "tests/fixtures/optimizer/tpc-h/tpc-h.sql":
        (42422, "8a10e67fceb92ef36c4cb0ab26f064a2761b2a06f8cf931e77bd71d71a9ef1a6"),
    "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql":
        (470672, "f584cd3a0dac63ca9654f74ef53011f4b9db7cad223f812de621dadb2ff982d4"),
}
# The first four optimizer/lineage requests are SQLMesh and README examples,
# which remain ordinary Git inputs. TPC-derived lineage IDs continue after them.
LINEAGE_OFFSET = 4


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    # Same encoding as the protected optimizer and lineage drivers.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def source(environment, relative):
    path = environment / UPSTREAM / relative
    raw = path.read_bytes()
    size, digest = SOURCES[relative]
    if path.is_symlink() or len(raw) != size or sha(raw) != digest:
        raise ValueError("Pinned SQLGlot source differs: " + relative)
    return raw.decode("utf-8")


def schema_literal(text, constant):
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == constant for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Missing schema literal: " + constant)


def statements(text, prefixes):
    # The pinned upstream fixture reader: drop blank and comment/marker lines,
    # then alternate original query and expected optimized SQL.
    filtered = "\n".join(line for line in text.splitlines() if line and not line.startswith(prefixes))
    return filtered.split(";")


def derive(environment):
    helpers = source(environment, "tests/helpers.py")
    tpch = statements(source(environment, "tests/fixtures/optimizer/tpc-h/tpc-h.sql"), ("--",))
    tpch = [tpch[index].strip() for index in range(0, len(tpch) - 1, 2)]
    tpcds = statements(source(environment, "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql"), ("--", "#"))
    originals = [tpcds[index].strip() for index in range(0, len(tpcds) - 1, 2)]
    expected = [tpcds[index].strip() for index in range(1, len(tpcds) - 1, 2)]
    if (len(tpch) != 22 or len(originals) != 99 or len(expected) != 99
            or any(query.startswith("#") for query in tpch + originals)):
        raise ValueError("Unexpected pinned TPC fixture shape")
    tpch_schema = schema_literal(helpers, "TPCH_SCHEMA")
    tpcds_schema = schema_literal(helpers, "TPCDS_SCHEMA")
    requests = [(f"sqlglot-tpch-{index:02d}", {"sql": sql, "schema": tpch_schema, "dialect": None})
                for index, sql in enumerate(tpch, 1)]
    requests += [(f"sqlglot-tpcds-{index:02d}", {"sql": sql, "schema": tpcds_schema, "dialect": None})
                 for index, sql in enumerate(originals, 1)]
    outputs = [("optimizer-" + name, encode(request)) for name, request in requests]
    outputs += [(f"lineage-public-{index:04d}", encode({**request, "id": f"public-{index:04d}"}))
                for index, (_, request) in enumerate(requests, LINEAGE_OFFSET)]
    outputs += [(f"diff-public-{index:04d}", encode({"id": f"public-{index:04d}", "answer": answer,
                                                     "references": [reference], "dialect": None}))
                for index, (answer, reference) in enumerate(zip(originals, expected))]
    return outputs


def commitments(environment):
    manifest = json.loads((environment / "workload/inputs/manifest.json").read_text())
    docs = {doc["path"]: doc for batches in manifest["splits"].values()
            for batch in batches for doc in batch["docs"]}
    packet = json.loads((environment / "public-input-packet.json").read_text())
    derived = {row["destination"]: row for row in packet["derived_inputs"]}
    return docs, derived


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    parser.add_argument("--check", action="store_true", help="Compare with commitments without writing")
    args = parser.parse_args(argv)
    environment = args.environment.resolve()
    docs, derived = commitments(environment)
    outputs = derive(environment)
    planned = {}
    for name, raw in outputs:
        doc = docs.get(name)
        destination = "environment/" + OUTPUT + "/" + sha(raw)
        row = derived.get(destination)
        if (doc is None or (doc["bytes"], doc["sha256"]) != (len(raw), sha(raw))
                or row is None or (row["bytes"], row["sha256"]) != (len(raw), sha(raw))):
            raise ValueError("Derived public input differs from its frozen commitment: " + name)
        planned[destination] = raw
    if set(planned) != set(derived):
        raise ValueError("Derived public inputs do not cover the packet commitments exactly")
    written = 0
    for destination, raw in planned.items():
        target = environment / destination.removeprefix("environment/")
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.read_bytes() != raw:
                raise ValueError("Refusing to replace different public input bytes: " + target.name)
            continue
        if args.check:
            continue
        temporary = target.with_name("." + target.name + ".deriving")
        with temporary.open("xb") as stream:
            stream.write(raw)
        os.replace(temporary, target)
        written += 1
    json.dump({"derived_public_inputs": len(planned), "written": written,
               "verified": "size and SHA-256 equal packet and manifest commitments"}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
