"""Distinct real-commit batches; only the shared runner owns clocks."""
import json
import re
import sys
from pathlib import Path
from runner import Case, Step, documents, canonical_json

ENV = {
    "LC_ALL": "C.UTF-8", "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ATTR_NOSYSTEM": "1", "GIT_PAGER": "cat",
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}

# Original three units; their cases, commands and outputs are unchanged.
ORIGINAL = ("patch-myers", "shortstat-myers", "patch-histogram")
# Further diff-tree --stdin units over distinct commits of other repositories.
DIFF_TREE = {
    "patch-ignore-all-space": ["--diff-algorithm=myers", "-p", "--ignore-all-space"],
    "patch-patience": ["--diff-algorithm=patience", "-p"],
    "patch-full-context": ["--diff-algorithm=myers", "-p", "-U32767"],
}
OTHER = ("merge-file",)
OID = re.compile(r"[0-9a-f]{40}")
PLAN = "derive-plan.json"


def cases(inputs):
    rows = documents(inputs)
    declarations = [json.loads((Path(inputs) / "files" / row["sha256"]).read_text())
                    for row in rows]
    seen = set()
    for declaration in declarations:
        for batch in declaration["repositories"]:
            for commit in batch.get("commits", ()):
                key = batch["id"], commit
                if key in seen:
                    raise ValueError("Repeated repository/commit across public units")
                seen.add(key)
    return [Case(row["path"], declaration) for row, declaration in zip(rows, declarations)]


def references(inputs):
    return [row | {"path": row["id"]} for row in json.loads(
        (Path(inputs).parent / "baseline-outputs.json").read_text())]


def history(ctx, batch):
    """Authenticated read-only canonical history selected by protected case data."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", batch["id"]):
        raise ValueError("Invalid repository id")
    return ctx.inputs / "histories" / batch["id"]


def git(ctx):
    return str(ctx.build / "project/git")


def prepare(case, ctx):
    family = case.data["family"]
    if family in ORIGINAL:
        return prepare_original(case, ctx)
    if family in DIFF_TREE:
        return prepare_diff_tree(case, ctx, DIFF_TREE[family])
    if family not in OTHER:
        raise ValueError("Unknown family")
    return globals()["prepare_" + family.replace("-", "_")](case, ctx)


def prepare_original(case, ctx):
    family = case.data["family"]
    if family not in ("patch-myers", "shortstat-myers", "patch-histogram"):
        raise ValueError("Unknown family")
    steps = []
    for index, batch in enumerate(case.data["repositories"]):
        if not batch["commits"] or len(set(batch["commits"])) != len(batch["commits"]):
            raise ValueError("Empty/repeated commit batch")
        name = f"commits-{index}.txt"
        (ctx.scratch / name).write_text("".join(c + "\n" for c in batch["commits"]))
        options = ["--shortstat"] if family == "shortstat-myers" else ["-p", "--binary", "--full-index"]
        algorithm = "histogram" if family == "patch-histogram" else "myers"
        steps.append(Step([
            str(ctx.build / "project/git"), "--no-pager", "-C",
            str(ctx.inputs / "histories" / batch["id"]),
            "diff-tree", "--stdin", "-r", "--root", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--no-color",
            "--diff-algorithm=" + algorithm, *options,
        ], stdin_file=name, timeout=600))
    return steps


def prepare_diff_tree(case, ctx, options):
    steps = []
    for index, batch in enumerate(case.data["repositories"]):
        if not batch["commits"] or len(set(batch["commits"])) != len(batch["commits"]):
            raise ValueError("Empty/repeated commit batch")
        name = f"commits-{index}.txt"
        (ctx.scratch / name).write_text("".join(c + "\n" for c in batch["commits"]))
        steps.append(Step([
            git(ctx), "--no-pager", "-C", str(history(ctx, batch)),
            "diff-tree", "--stdin", "-r", "--root", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--no-color", *options,
        ], stdin_file=name, timeout=600))
    return steps


def derive(ctx, plan):
    """Untimed derivations by the image's pristine Git, never the build under test.

    Docker cases: the protected container_prepare hook applies the plan in the
    fresh case container before any timed step. Local runs apply it here."""
    (ctx.scratch / PLAN).write_text(json.dumps(plan, sort_keys=True))
    if ctx.docker:
        return
    sys.path.insert(0, str(ctx.workload / "guards"))
    try:
        import derive_inputs
    finally:
        sys.path.pop(0)
    derive_inputs.apply(plan, ctx.scratch, ctx.inputs)


def prepare_merge_file(case, ctx):
    steps, extract = [], []
    for index, batch in enumerate(case.data["repositories"]):
        files = []
        for number, triple in enumerate(batch["triples"]):
            if len(triple["blobs"]) != 3 or not all(OID.fullmatch(b) for b in triple["blobs"]):
                raise ValueError("Invalid merge-file triple")
            directory = f"merge-{index}/{number:05d}"
            (ctx.scratch / directory).mkdir(parents=True)
            # Blob order is base, ours, theirs as frozen by the candidate kit.
            files += [[oid, f"{directory}/{name}"] for oid, name in
                      zip(triple["blobs"], ("base", "ours", "theirs"))]
            steps.append(Step([git(ctx), "merge-file", "-p", "-L", "ours", "-L", "base",
                               "-L", "theirs", "ours", "base", "theirs"],
                              cwd=directory, timeout=600))
        extract.append({"objects": ctx.target(history(ctx, batch) / ".git/objects"), "files": files})
    derive(ctx, {"blobs": extract, "warm": [ctx.target(history(ctx, b)) for b in case.data["repositories"]]})
    return steps


def collect(case, ctx, results):
    if case.data["family"] == "merge-file":
        expected = sum(len(batch["triples"]) for batch in case.data["repositories"])
    else:
        expected = len(case.data["repositories"])
    if len(results) != expected:
        raise ValueError("Missing repository result")
    return {case.id: canonical_json({"calls": [r.streams() for r in results]})}


def compare(expected, observed):
    return expected == observed
