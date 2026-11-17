"""Original codec operations and complete small-memory book decoding."""
from pathlib import Path
import shutil
from runner import Case, Step, locked_cases, bundle_references, collected_bundle, contained

ENV = {"OMP_NUM_THREADS": "8"}
RESULT_ENCODING = "named-files-v1"
def references(inputs):
    originals = list(bundle_references(inputs))
    return [*originals, *[
        {**row, "path": "batch--" + row["path"]}
        for row in originals if not row["path"].startswith(("guard-", "small-text-"))],
        *[{**row, "path": "small-text--" + row["path"]}
          for row in originals if row["path"].startswith("small-text-")]]


def cases(inputs):
    originals = list(locked_cases(inputs))
    members = [case for case in originals
               if not case.id.startswith(("guard-", "small-text-"))]
    small = [case for case in originals if case.id.startswith("small-text-")]
    # Each frozen, distinct original operation occurs exactly once in the timed
    # native batch. Individual originals remain mandatory correctness cases.
    if not members or len({case.id for case in members}) != len(members):
        raise ValueError("Missing or duplicate distinct native batch members")
    batches = [Case("distinct-native-batch", {
        "members": [{"id": case.id, **case.data} for case in members],
        "docs": [{"path": "batch--" + case.id} for case in members],
        "family": "distinct-natural-workload",
    })]
    if small:
        batches.append(Case("small-text-batch", {
            "members": [{"id": case.id, **case.data} for case in small],
            "docs": [{"path": "small-text--" + case.id} for case in small],
            "family": "small-memory-text",
            "prefix": "small-text--",
        }))
    return [*originals, *batches]


def prepare(case, ctx):
    row = case.data
    if "members" in row:
        lines = []
        for member in row["members"]:
            name = member["input"]
            source = contained(ctx.inputs / "data", name)
            destination = contained(ctx.scratch / "inputs", name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            lines.append("\t".join(str(value) for value in (
                member["id"], member["direction"], name, member["level"],
                member.get("small", 0), member.get("chunk", 65536))))
        (ctx.scratch / "inputs/batch.tsv").write_text("\n".join(lines) + "\n")
        (ctx.scratch / "results").mkdir()
        return [Step([str(ctx.build / "codec"), "once", "batch.tsv"],
                     env={"BZIP2_INPUT_ROOT": str(ctx.scratch / "inputs"),
                          "BZIP2_OUTPUT_ROOT": str(ctx.scratch / "results")})]
    shutil.copyfile(ctx.inputs / "data" / row["input"], ctx.scratch / "input.bin")
    env = ({"BZIP2_ALLOCATION_LIMIT": str(row["allocation_limit"])}
           if "allocation_limit" in row else {})
    return [Step([str(ctx.build / "codec"), "one", row["direction"], "input.bin", "result",
                  str(row["level"]), str(row.get("small", 0)),
                  str(row.get("chunk", 65536)), "v1"], env=env)]


def collect(case, ctx, results):
    if "members" in case.data:
        outputs = {}
        for member in case.data["members"]:
            original = Case(member["id"], member)
            collected = collected_bundle(original, ctx, results, {
                "result.bin": contained(ctx.scratch / "results", member["id"] + ".bin"),
                "status.json": contained(ctx.scratch / "results", member["id"] + ".status.json")})
            outputs.update({case.data.get("prefix", "batch--") + name: value
                            for name, value in collected.items()})
        expected = {member["id"] + suffix for member in case.data["members"]
                    for suffix in (".bin", ".status.json")}
        if {path.name for path in (ctx.scratch / "results").iterdir()} != expected:
            raise ValueError("Unexpected native batch output inventory")
        return outputs
    return collected_bundle(case, ctx, results, {
        "result.bin": ctx.scratch / "result.bin",
        "status.json": ctx.scratch / "result.status.json"})
