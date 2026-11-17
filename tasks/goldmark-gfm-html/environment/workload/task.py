"""Shared adapter template copied into each mined task (not a runner)."""
import json
from pathlib import Path
from runner import Case, Step, documents, canonical_json, b64

ENV = {"GOMAXPROCS": "8", "OMP_NUM_THREADS": "8", "LC_ALL": "C", "TZ": "UTC"}


def cases(inputs):
    docs = documents(inputs)
    # Batch membership is frozen in the manifest; correctness remains per input.
    groups = {}
    for doc in docs:
        groups.setdefault(doc["batch"], []).append(doc)
    return [Case(name, {"docs": members}) for name, members in groups.items()]


def prepare(case, ctx):
    docs = case.data["docs"]
    ctx.batch([{key: doc[key] for key in ("path", "sha256", "bytes")} for doc in docs])
    kind = json.loads((ctx.workload / "layout.json").read_text())["kind"]
    if kind == "ctags":
        doc = docs[0]
        target = ctx.scratch / doc["original_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ctx.scratch / doc["sha256"]).read_bytes())
        return [Step([str(ctx.build / "ctags-bench"), doc["original_path"],
                      str(ctx.scratch / (doc["path"] + ".out"))])]
    if kind == "xz":
        return [Step([str(ctx.build / "xz-driver"), str(ctx.scratch / docs[0]["sha256"])])]
    binary = {"lame": "driver-runner", "goldmark": "goldmark-gfm-html",
              "jq": "driver", "charls": "charls-driver"}[kind]
    if kind == "goldmark" and case.id == "parse-only":
        # Second performance unit: the same documents through the protected
        # parse-only AST-walk driver (native/parse/main.go), no HTML rendering.
        binary = "goldmark-parse"
    # Rows hold IDs and scratch-local input names, never expected output metadata.
    rows = []
    for doc in docs:
        row = [doc["path"], doc["sha256"]]
        if kind == "charls":
            row += [str(doc["width"]), str(doc["height"])]
        rows.append("\t".join(row))
    (ctx.scratch / "rows.tsv").write_text("\n".join(rows) + "\n")
    return [Step([str(ctx.build / binary), "rows.tsv"], timeout=1800)]


def collect(case, ctx, results):
    if json.loads((ctx.workload / "layout.json").read_text())["kind"] == "xz":
        result = results[0]
        return {case.data["docs"][0]["path"]: (
            result.stdout if result.exit_code == 0 and not result.stderr else
            canonical_json({"format": "workload-failure-v1", **result.streams()}))}
    return ctx.batch_results(case.data["docs"], results)
