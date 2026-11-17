"""SQLFluff project cases; canonical sqlfluff-output-v1 bytes."""
import json
import importlib.util
from pathlib import Path
from runner import Step, b64, canonical_json, project_cases

_spec = importlib.util.spec_from_file_location(
    "sqlfluff_project_autofix", Path(__file__).parent / "driver/autofix.py")
autofix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(autofix)
_spec = importlib.util.spec_from_file_location(
    "sqlfluff_project_alternates", Path(__file__).parent / "driver/alternates.py")
alternates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alternates)

ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONSAFEPATH": "1",
       "PYTHONWARNINGS": "ignore::UserWarning:multiprocessing.resource_tracker"}
cases = project_cases


def prepare(case, ctx):
    if case.data.get("operation") == "autofix":
        return autofix.prepare(case, ctx)
    if case.data.get("operation") in ("snowflake-lint", "syntax-tree"):
        return alternates.prepare(case, ctx, autofix.snapshot)
    if "stages" in case.data:
        steps = []
        for index, stage in enumerate(case.data["stages"]):
            destination = f"stage-{index}/project"
            ctx.copy_project(stage["project"], stage["inventory_sha256"], destination)
            steps.append(Step(
                ["/usr/local/bin/python", "-m", "sqlfluff", *stage["args"]], destination,
                {"PYTHONPATH": str(ctx.build / "runtime"),
                 "XDG_CACHE_HOME": str(ctx.scratch / f"stage-{index}/cache"),
                 "XDG_CONFIG_HOME": str(ctx.scratch / f"stage-{index}/config")},
                stage["timeout"]))
        return steps
    ctx.copy_project(case.data["project"], case.data["inventory_sha256"])
    return [Step(["/usr/local/bin/python", "-m", "sqlfluff", *case.data["args"]], "project",
                 {"PYTHONPATH": str(ctx.build / "runtime"),
                  "XDG_CACHE_HOME": str(ctx.scratch / "cache"),
                  "XDG_CONFIG_HOME": str(ctx.scratch / "config")}, case.data["timeout"])]


def _record(result):
    record = {"format": "sqlfluff-output-v1", "exit_code": result.exit_code,
              "stderr_base64": b64(result.stderr)}
    try:
        rows = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        rows = None
    if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
        record["stdout_records"] = [{k: v for k, v in row.items() if k != "timings"} for row in rows]
    else:
        record["stdout_base64"] = b64(result.stdout)
    return record


def collect(case, ctx, results):
    if case.data.get("operation") == "autofix":
        return autofix.collect(case, ctx, results)
    if case.data.get("operation") in ("snowflake-lint", "syntax-tree"):
        return alternates.collect(case, ctx, results, autofix.snapshot)
    if "stages" in case.data:
        if len(results) != len(case.data["stages"]):
            raise ValueError("Every declared cold stage must run exactly once")
        record = {"format": "sqlfluff-project-unit-v1",
                  "stages": [{"project": stage["project"], "output": _record(result)}
                             for stage, result in zip(case.data["stages"], results)]}
    else:
        record = _record(results[0])
    return {case.id: canonical_json(record, ensure_ascii=False)}
