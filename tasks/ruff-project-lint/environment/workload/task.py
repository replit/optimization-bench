"""Ruff disjoint CLI batches; canonical cli-output-v1 bytes."""
from runner import Step, canonical_json, project_cases

ENV = {"RAYON_NUM_THREADS": "8", "NO_COLOR": "1"}
cases = project_cases


def prepare(case, ctx):
    # The recorded upstream Git tree binds every source file, not fetch logs or packfiles.
    # Preserve .git for the original ignore-discovery semantics.
    ctx.copy_project(case.data["project"], case.data["inventory_sha256"], exclude_vcs=True)
    return [Step([str(ctx.build / "ruff"), *command], "project", timeout=600)
            for command in case.data["commands"]]


def collect(case, ctx, results):
    return {case.id: canonical_json({"format": "cli-output-v1",
                                    "runs": [result.streams() for result in results]})}
