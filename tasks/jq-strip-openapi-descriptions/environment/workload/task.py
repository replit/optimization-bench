"""Shared adapter template copied into each mined task (not a runner)."""
import gzip
import hashlib
import json
from pathlib import Path
from runner import Case, Step, documents, canonical_json, b64

ENV = {"GOMAXPROCS": "8", "OMP_NUM_THREADS": "8", "LC_ALL": "C", "TZ": "UTC"}
STREAM_FILTER = 'fromstream(2 | truncate_stream(inputs | select(.[0][0] == "features")))'
GROUP_FILTER = ('[inputs | [.repo.name, .type]] | group_by(.) | '
                'map({repository: .[0][0], event_type: .[0][1], count: length}) | '
                'sort_by([-.count, .repository, .event_type])')
CSV_FILTER = '[.id, .type, .repo.name, .created_at] | @csv'
CLI_OPERATIONS = {
    "stream-geojson": ("input.geojson", ["-cn", "--stream", STREAM_FILTER]),
    "group-gharchive": ("events.jsonl", ["-nc", GROUP_FILTER]),
    "csv-gharchive": ("events.jsonl", ["-r", CSV_FILTER]),
}


def stage_archive(source, destination, doc):
    """Authenticate the complete decoded hour outside the measured process."""
    expected = doc["decoded_bytes"]
    if type(expected) is not int or expected <= 0:
        raise ValueError("Invalid decoded input size")
    digest, size = hashlib.sha256(), 0
    with gzip.open(source, "rb") as archive, destination.open("wb") as output:
        while chunk := archive.read(min(1024 * 1024, expected + 1 - size)):
            size += len(chunk)
            if size > expected:
                raise ValueError("Decoded input exceeds its frozen size")
            digest.update(chunk)
            output.write(chunk)
    if size != expected or digest.hexdigest() != doc["decoded_sha256"]:
        raise ValueError("Decoded input differs from its frozen commitment")


def cases(inputs):
    docs = documents(inputs)
    # Batch membership is frozen in the manifest; correctness remains per input.
    groups = {}
    for doc in docs:
        groups.setdefault(doc["batch"], []).append(doc)
    for members in groups.values():
        operations = {doc.get("operation", "strip-descriptions") for doc in members}
        if len(operations) != 1 or not operations <= {"strip-descriptions", *CLI_OPERATIONS}:
            raise ValueError("Unknown or mixed workload operations")
        if operations <= CLI_OPERATIONS.keys() and len(members) != 1:
            raise ValueError("A CLI case contains one original input")
    return [Case(name, {"docs": members}) for name, members in groups.items()]


def prepare(case, ctx):
    docs = case.data["docs"]
    ctx.batch([{key: doc[key] for key in ("path", "sha256", "bytes")} for doc in docs])
    operation = docs[0].get("operation")
    if operation in CLI_OPERATIONS:
        # One fresh CLI process, using the same source-aware build as the native
        # driver. Input staging is outside execution. Complete parsing, filter
        # compilation/evaluation and output materialization remain inside it.
        filename, arguments = CLI_OPERATIONS[operation]
        source, destination = ctx.scratch / docs[0]["sha256"], ctx.scratch / filename
        if operation == "stream-geojson":
            destination.write_bytes(source.read_bytes())
        else:
            stage_archive(source, destination, docs[0])
        libs = [ctx.build / "project/.libs",
                ctx.build / "project/vendor/oniguruma/src/.libs"]
        return [Step([str(ctx.build / "project/.libs/jq"), *arguments, filename],
                     env={"LD_LIBRARY_PATH": ":".join(map(ctx.target, libs))}, timeout=1800)]
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
    if case.data["docs"][0].get("operation") in CLI_OPERATIONS:
        if len(results) != 1:
            raise ValueError("One complete CLI observation is required")
        return {case.data["docs"][0]["path"]: canonical_json(results[0].streams())}
    if json.loads((ctx.workload / "layout.json").read_text())["kind"] == "xz":
        result = results[0]
        return {case.data["docs"][0]["path"]: (
            result.stdout if result.exit_code == 0 and not result.stderr else
            canonical_json({"format": "workload-failure-v1", **result.streams()}))}
    return ctx.batch_results(case.data["docs"], results)
