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
        steps = []
        for doc in docs:
            target = ctx.scratch / doc["original_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ctx.scratch / doc["sha256"]).read_bytes())
            steps.append(Step([str(ctx.build / "ctags-bench"), doc["original_path"],
                               str(ctx.scratch / (doc["path"] + ".out"))]))
        return steps
    if kind == "xz":
        # Documents without an operation are the original preset-6 compression cases.
        drivers = {None: "xz-driver", "stream-decode": "xz-decode"}
        return [Step([str(ctx.build / drivers[doc.get("operation")]), str(ctx.scratch / doc["sha256"])])
                for doc in docs]
    binary = {"lame": "driver-runner", "goldmark": "goldmark-gfm-html",
              "jq": "driver", "charls": "charls-driver"}[kind]
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
    kind = json.loads((ctx.workload / "layout.json").read_text())["kind"]
    if kind in ("xz", "ctags"):
        outputs = {}
        for doc, result in zip(case.data["docs"], results, strict=True):
            if kind == "ctags":
                outputs.update(ctx.batch_results([doc], [result]))
            else:
                outputs[doc["path"]] = (
                    result.stdout if result.exit_code == 0 and not result.stderr else
                    canonical_json({"format": "workload-failure-v1", **result.streams()}))
        return outputs
    return ctx.batch_results(case.data["docs"], results)
