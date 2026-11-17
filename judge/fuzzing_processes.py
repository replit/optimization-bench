"""Narrow CPython runtime-helper witnesses, always conditioned on pristine facts."""
import ast
from collections import Counter
import json
from pathlib import PurePosixPath
import re
import shlex


def runtime_helper(process):
    command = process.get("cmdline")
    if not isinstance(command, str) or " -c " not in command:
        return None
    prefix, source = command.split(" -c ", 1)
    try:
        executable, *flags = shlex.split(prefix)
        if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", PurePosixPath(executable).name):
            return None
        tree = ast.parse(source.strip())
        if len(tree.body) != 2:
            return None
        imported, expression = tree.body
        if (not isinstance(imported, ast.ImportFrom) or imported.level != 0
                or imported.module not in ("multiprocessing.resource_tracker", "multiprocessing.forkserver")
                or len(imported.names) != 1 or imported.names[0].name != "main"
                or imported.names[0].asname is not None or not isinstance(expression, ast.Expr)):
            return None
        call = expression.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "main":
            return None
        args = [ast.literal_eval(value) for value in call.args]
        kwargs = {value.arg or "**": ast.literal_eval(value.value) for value in call.keywords}
        fd_count = 1 if imported.module.endswith("resource_tracker") else 2
        if len(args) < fd_count or any(type(value) is not int or value < 0 for value in args[:fd_count]):
            return None
        if fd_count == 1 and (len(args) != 1 or kwargs):
            return None
        # Ignore only descriptor numbers; keep interpreter flags/preload/context.
        return json.dumps({"module": imported.module, "interpreter": executable,
                           "flags": flags, "args_after_fds": args[fd_count:], "kwargs": kwargs},
                          sort_keys=True)
    except (ValueError, SyntaxError, TypeError):
        return None


def classify_pairs(pairs, identical_pristine=False):
    """No free-standing name exemption: a matching pristine witness is required."""
    snapshots = []
    for pair in pairs:
        row = {}
        for arm in ("baseline", "candidate"):
            helpers, users = Counter(), []
            for process in pair[arm].get("stopped_descendants", []):
                helper = runtime_helper(process)
                if helper is None:
                    users.append(process)
                else:
                    helpers[helper] += 1
            row[arm] = {"helpers": helpers, "users": users}
        snapshots.append(row)
    baseline = [row["baseline"]["helpers"] for row in snapshots]
    if any(row["baseline"]["users"] for row in snapshots):
        return {"status": "baseline_nonrepeatable", "reason": "Pristine user process survived the operation"}
    if any(value != baseline[0] for value in baseline[1:]):
        return {"status": "baseline_nonrepeatable", "reason": "Pristine runtime-helper shutdown is intermittent"}
    extra = any(row["candidate"]["users"] or
                any(count > baseline[0][key] for key, count in row["candidate"]["helpers"].items())
                for row in snapshots)
    if extra and identical_pristine:
        return {"status": "baseline_nonrepeatable",
                "reason": "Identical pristine implementations differ in shutdown-helper presence"}
    return {"status": "needs_review" if extra else "passed",
            "reason": "Additional process without matching pristine runtime-helper presence" if extra else
                      "Runtime-helper presence matched pristine",
            "runtime_helpers": [dict(value) for value in baseline]}
