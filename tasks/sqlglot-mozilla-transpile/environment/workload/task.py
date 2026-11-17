"""Four cold batches, one rebuilt package, complete per-input results."""
from runner import Case, HarnessError, Step, documents

ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONSAFEPATH": "1"}


def cases(inputs):
    groups = {"transpile": [], "optimizer": [], "lineage": [], "diff": []}
    for doc in documents(inputs):
        family = doc.get("family", "transpile")
        if family not in groups:
            raise HarnessError("Unknown SQLGlot input family")
        groups[family].append(doc)
    if not all(groups.values()):
        raise HarnessError("Both original conversion and optimizer inputs are required")
    return [
        Case("batch", {"docs": groups["transpile"], "child": "child.py"}),
        Case("optimizer", {"docs": groups["optimizer"], "child": "optimizer_child.py"}),
        Case("lineage", {"docs": groups["lineage"], "child": "analysis_child.py"}),
        Case("diff", {"docs": groups["diff"], "child": "analysis_child.py"}),
    ]


def prepare(case, ctx):
    ctx.batch(case.data["docs"])
    return [Step(["/usr/local/bin/python", str(ctx.workload / "driver" / case.data["child"]),
                  str(ctx.build / "runtime"), str(ctx.scratch)],
                 env={"PYTHONPATH": str(ctx.build / "runtime"),
                      "XDG_CACHE_HOME": str(ctx.scratch / "cache"),
                      "XDG_CONFIG_HOME": str(ctx.scratch / "config")}, timeout=5400)]


def collect(case, ctx, results):
    return ctx.batch_results(case.data["docs"], results)
