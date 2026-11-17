"""Protected clients for complete Snowflake lint and BigQuery syntax exports."""
import hashlib
import json
from pathlib import Path
import re

from runner import Step, b64, canonical_json, contained, inventory_sha256

BEFORE = "alternate-before.json"


def capture(scratch, snapshot):
    (scratch / BEFORE).write_text(json.dumps(snapshot(scratch / "project"), sort_keys=True))


def relative(name):
    path = Path(name)
    if path.is_absolute() or path.as_posix() != name or ".." in path.parts or name.startswith("-"):
        raise ValueError("Alternate inputs require canonical relative paths")
    return name


def prepare(case, ctx, snapshot):
    project = ctx.copy_project(case.data["project"], case.data["inventory_sha256"])
    for name, digest in case.data.get("shared_files", {}).items():
        destination = contained(project, relative(name))
        if not re.fullmatch("[0-9a-f]{64}", digest) or destination.exists():
            raise ValueError("Invalid protected metadata")
        raw = (ctx.workload / "driver/alternate-assets" / digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Protected metadata commitment mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    if inventory_sha256(project) != case.data["complete_inventory_sha256"]:
        raise ValueError("Complete alternate input inventory mismatch")
    targets = case.data["targets"]
    if not targets or len(targets) != len(set(targets)):
        raise ValueError("Each original SQL file must occur exactly once")
    for name in targets:
        path = contained(project, relative(name))
        if not path.is_file() or path.is_symlink():
            raise ValueError("Alternate targets must be original regular files")
    for path in (project, *project.rglob("*")):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | (0o777 if path.is_dir() else 0o666))
    capture(ctx.scratch, snapshot)
    if case.data["operation"] == "snowflake-lint":
        argv = ["/usr/local/bin/python", "-m", "sqlfluff", "lint", *targets,
                "--dialect=snowflake", "--templater=jinja", "--processes", "1",
                "--format", "json", "--disable-progress-bar", "--nocolor",
                "--ignore-local-config", "--config", relative(case.data["config"])]
    elif case.data["operation"] == "syntax-tree":
        argv = ["/usr/local/bin/python", str(ctx.workload / "driver/export_tree.py"), *targets]
    else:
        raise ValueError("Unknown alternate operation")
    return [Step(argv, "project", {
        "PYTHONPATH": str(ctx.build / "runtime"),
        "XDG_CACHE_HOME": str(ctx.scratch / "cache"),
        "XDG_CONFIG_HOME": str(ctx.scratch / "config"),
    }, case.data["timeout"])]


def collect(case, ctx, results, snapshot):
    if len(results) != 1:
        raise ValueError("Every alternate operation must run exactly once")
    before = json.loads((ctx.scratch / BEFORE).read_text())
    after = snapshot(ctx.scratch / "project")
    # Physical metadata is checked within this operation, never across hosts.
    stable = lambda files: {name: {k: v for k, v in row.items() if k != "atime_ns"}
                            for name, row in files.items()}
    raw = (ctx.scratch / "step-0.stdout").read_bytes()
    try:
        output = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        output = {"malformed_stdout_base64": b64(raw)}
    if (case.data["operation"] == "snowflake-lint" and isinstance(output, list)
            and all(isinstance(row, dict) for row in output)):
        output = [{k: v for k, v in row.items() if k != "timings"} for row in output]
    record = {
        "exit_code": results[0].exit_code, "output": output,
        "stderr_base64": b64((ctx.scratch / "step-0.stderr").read_bytes()),
        "inputs_unchanged": stable(before) == stable(after),
    }
    return {case.id: canonical_json(record)}
