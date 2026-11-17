"""Black project cases; canonical black-output-v1 bytes.

Project cases (`black-case-v1`) run the original CLI or pre-commit hook and are unchanged.
API documents (`black-api-case-v1`, added 2026-10-29) are grouped by their `case` field (default:
their own id) into one resident warm process per case (driver/api_batch.py): `snippets` calls
black.format_str per documentation fragment; `ranges` calls black.format_file_contents(..., lines=...)
per editor range request. Each case keeps one result per authenticated document, and its measured
interval is the serial call loop only (resident SIGSTOP protocol).
"""
from runner import Case, Step, b64, canonical_json, project_cases

ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONSAFEPATH": "1",
       "NO_COLOR": "1", "BLACK_NUM_WORKERS": "8"}
API_FORMAT = "black-api-case-v1"


def cases(inputs):
    result, groups = [], {}
    for case in project_cases(inputs):
        if case.data.get("format") != API_FORMAT:
            result.append(case)
            continue
        name = case.data.get("case", case.id)
        if name not in groups:
            groups[name] = Case(name, {"format": API_FORMAT, "docs": [], "timeout": 0})
            result.append(groups[name])
        group = groups[name].data
        group["docs"].append({**case.data, "path": case.id})
        group["timeout"] = max(group["timeout"], case.data["timeout"])
    return result


def prepare(case, ctx):
    if case.data.get("format") == API_FORMAT:
        return prepare_api(case, ctx)
    tree = ctx.copy_project(case.data["project"], case.data["inventory_sha256"])
    (tree / ".git").mkdir(exist_ok=True)
    request = ctx.scratch / "case.json"
    request.write_bytes(canonical_json(case.data))
    command = (["/usr/local/bin/python", str(ctx.workload / "driver/black_hook.py"), str(request)]
               if case.data.get("hook") else ["/usr/local/bin/python", "-m", "black", *case.data["args"]])
    return [Step(command, "project", {
        "PYTHONPATH": str(ctx.build / "runtime"), "BLACK_CACHE_DIR": str(ctx.scratch / "cache"),
        "XDG_CACHE_HOME": str(ctx.scratch / "cache"), "XDG_CONFIG_HOME": str(ctx.scratch / "config")
    }, case.data["timeout"])]


def collect(case, ctx, results):
    if case.data.get("format") == API_FORMAT:
        return collect_api(case, ctx, results)
    result = results[0]
    files, links = ctx.snapshot(ctx.scratch / "project", skip_git=True)
    # Black reports files it would reformat by absolute path; the scratch location is not contract data.
    root = (ctx.target(ctx.scratch / "project") + "/").encode()
    stdout, stderr = result.stdout.replace(root, b""), result.stderr.replace(root, b"")
    return {case.id: canonical_json({
        "format": "black-output-v1", "exit_code": result.exit_code,
        "stdout_base64": b64(stdout),
        "stderr_lines_base64": [b64(line) for line in sorted(stderr.splitlines())],
        "files_base64": files, "symlinks": links})}


def prepare_api(case, ctx):
    request = ctx.scratch / "case.json"
    request.write_bytes(canonical_json({"docs": case.data["docs"]}))
    (ctx.scratch / "results").mkdir()
    return [Step(["/usr/local/bin/python", str(ctx.workload / "driver/api_batch.py"),
                  str(request), str(ctx.scratch / "results")], ".", {
        "PYTHONPATH": str(ctx.build / "runtime"), "BLACK_CACHE_DIR": str(ctx.scratch / "cache"),
        "XDG_CACHE_HOME": str(ctx.scratch / "cache"), "XDG_CONFIG_HOME": str(ctx.scratch / "config")
    }, case.data["timeout"], resident=True)]


def collect_api(case, ctx, results):
    result = results[0]
    protocol = result.telemetry.get("execution_protocol")
    paths = [doc["path"] for doc in case.data["docs"]]
    outputs = [ctx.scratch / "results" / ("result-%d.json" % i) for i in range(len(paths))]
    if (result.exit_code == 0 and not result.stdout and not result.stderr and protocol == "complete"
            and all(o.is_file() and not o.is_symlink() for o in outputs)):
        return {path: output.read_bytes() for path, output in zip(paths, outputs)}
    failure = canonical_json({"format": "workload-failure-v1", **result.streams(), "execution_protocol": protocol})
    return {path: failure for path in paths}
