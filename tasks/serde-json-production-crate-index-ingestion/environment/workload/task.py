"""One resident process per distinct-input corpus; no repetition or padding.

Two protected units: the registry corpus (crates-index typed ingestion, unchanged)
and geojson-typed (typed RFC 7946 GeoJSON through serde_json into exact flat output).
Manifest batches select the unit by id; a manifest without ids is the registry unit."""
import hashlib
import json
from pathlib import Path
from runner import Case, Step, HarnessError, canonical_json, documents

ENV = {"LC_ALL": "C.UTF-8", "TZ": "UTC"}
REGISTRY, GEOJSON = "distinct-registry-corpus", "geojson-typed-corpus"
# Per unit: protected program and its complete-result file suffix.
PROGRAMS = {REGISTRY: ("prog", ".json"), GEOJSON: ("prog-geojson", ".bin")}


def strict_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def descriptor(inputs):
    return json.loads((Path(inputs) / "manifest.json").read_text())


def cases(inputs):
    docs = documents(inputs)
    if (len({doc["sha256"] for doc in docs}) != len(docs) or
            len({doc["path"] for doc in docs}) != len(docs)):
        raise HarnessError("Realism units require distinct files and output identities")
    groups = {}
    for batches in descriptor(inputs)["splits"].values():
        for batch in batches:
            groups.setdefault(batch.get("id", REGISTRY), []).extend(batch["docs"])
    if REGISTRY not in groups or set(groups) - set(PROGRAMS):
        raise HarnessError("Unknown or missing protected unit batch")
    return [Case(unit, {"docs":groups[unit]}) for unit in PROGRAMS if unit in groups]


def references(inputs):
    docs = documents(inputs)
    return [{"path": doc["path"],
             "expect_len": doc["expect_len"], "expect_sha": doc["expect_sha"]}
            for doc in docs]


def prepare(case, ctx):
    docs = case.data["docs"]
    entries = []
    for doc in docs:
        raw = (ctx.inputs / "files" / doc["sha256"]).read_bytes()
        if len(raw) != doc["bytes"] or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise HarnessError("original input changed")
        (ctx.scratch / doc["sha256"]).write_bytes(raw)
        entries.append(doc["path"]+"\t"+ctx.target(ctx.scratch/doc["sha256"])+"\n")
    (ctx.scratch/"batch-list.tsv").write_text("".join(entries))
    return [Step([str(ctx.build/PROGRAMS[case.id][0]),"--resident-batch",
                  ctx.target(ctx.scratch/"batch-list.tsv"),
                  ctx.target(ctx.scratch/"results")],timeout=1800,resident=True)]


def collect(case, ctx, results):
    docs = case.data["docs"]
    if len(results) != 1:
        raise HarnessError("A realism unit must be exactly one resident process")
    result = results[0]
    outputs = {}
    suffix = PROGRAMS[case.id][1]
    expected = {doc["path"]+suffix for doc in docs}
    directory = ctx.scratch/"results"
    actual = {path.name for path in directory.iterdir()} if (
        directory.is_dir() and not directory.is_symlink()) else set()
    abnormal = (result.exit_code != 0 or result.stdout or result.stderr or
                result.leftovers or directory.is_symlink() or actual != expected or
                result.telemetry.get("execution_protocol") not in (None,"complete"))
    for doc in docs:
        name = doc["path"]
        path = directory/(name+suffix)
        if abnormal or not path.is_file() or path.is_symlink():
            outputs[name] = canonical_json({"format": "workload-failure-v1", **result.streams()})
            continue
        if case.id == GEOJSON:
            # Exact bytes: every coordinate's f64 bit pattern, property value and nesting.
            outputs[name] = path.read_bytes()
            continue
        try:
            # Complete outputs only; canonicalization removes key/escape spelling
            # differences, preserving all types, values, fields and array order.
            outputs[name] = canonical_json(strict_loads(path.read_bytes()))
        except (ValueError, UnicodeError):
            outputs[name] = canonical_json({"format": "workload-failure-v1", **result.streams()})
    return outputs
