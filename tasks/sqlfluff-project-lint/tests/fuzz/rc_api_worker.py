#!/usr/bin/env python3
"""One real in-process Linter worker; independent JSON on a private report FD."""
import argparse
import gc
import json
import os
from pathlib import Path
import sys

from adapter import TASK
from driver import capture_library_streams
from rc_api_cases import precondition


def caller_flags(config):
    return {"gc_enabled": gc.isenabled(), "gc_threshold": list(gc.get_threshold()),
            "recursion_limit": sys.getrecursionlimit(),
            "public_config": {k: config.get(k) for k in ("dialect", "rules", "templater")}}


def operation(case, primary, config, events):
    from sqlfluff.core import Linter
    linter = primary if case["options"]["instance"] == "primary" else Linter(dialect="ansi")
    sql = case["input"]["sql"]
    if case["operation"] == "parse":
        parsed = linter.parse_string(sql, fname="case.sql", config=config)
        return {"tree": parsed.tree.to_tuple(show_raw=True) if parsed.tree else None,
                "raw": parsed.tree.raw if parsed.tree else None,
                "violations": [v.to_dict() for v in parsed.violations]}, {}
    if case["operation"] == "lint":
        linted = linter.lint_string(sql, fname="case.sql", fix=case["options"]["fix"], config=config)
        return {"violations": [v.to_dict() for v in linted.get_violations()],
                "fixed": linted.fix_string() if case["options"]["fix"] else None}, {}
    root = Path("/tmp/sqlfluff-public-api-files")
    root.mkdir(exist_ok=True)
    path = root / "case.sql"
    path.write_text(sql, encoding="utf-8")
    fault = next(iter(case["faults"]), None)
    if fault == "missing":
        path.unlink()
    elif fault == "unreadable":
        if os.geteuid() == 0:
            raise ValueError("unreadable-file control requires unprivileged UID")
        path.chmod(0)
    if fault in ("missing", "unreadable", "parse-violation"):
        result = Linter(config=config).lint_paths(
            (str(path),), processes=1, fix=fault == "parse-violation",
            apply_fixes=fault == "parse-violation")
        records = [{k: v for k, v in r.items() if k != "timings"} for r in result.as_records()]
        return {"records": records, "violations": [v.to_dict() for v in result.get_violations()]}, {}
    linted = linter.lint_string(sql, fname=str(path), fix=True, config=config,
                                encoding=case["options"]["encoding"])
    before = [v.to_dict() for v in linted.get_violations()]
    events.append({"event": "before-public-persist", "violations": before,
                   "fixed": linted.fix_string()})
    persisted = linted.persist_tree(suffix=".fixed")
    return {"violations": before, "fixed": linted.fix_string(), "persisted": persisted}, {}


def file_effects():
    root = Path("/tmp/sqlfluff-public-api-files")
    if not root.exists():
        return {}
    import base64
    result = {}
    for path in sorted(root.iterdir()):
        if path.is_symlink():
            result[path.name] = {"kind": "symlink", "target": os.readlink(path)}
            continue
        if not path.is_file():
            result[path.name] = {"kind": "non-file", "mode": path.stat().st_mode & 0o777}
            continue
        mode, readable = path.stat().st_mode & 0o777, os.access(path, os.R_OK)
        try:
            if not readable:
                # Independent owner observation AFTER the library call. Restore
                # the exact observed mode; never alter the fault during the call.
                path.chmod(mode | 0o400)
            data = path.read_bytes()
        finally:
            if not readable:
                path.chmod(mode)
        result[path.name] = {"kind": "file", "bytes_base64": base64.b64encode(data).decode(),
                             "readable_after_call": readable, "mode": mode}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--report-fd", type=int, required=True)
    parser.add_argument("--build-sha256", required=True)
    args = parser.parse_args()
    if args.report_fd <= 2:
        raise ValueError("separate inherited report FD required")
    request = json.loads(args.request.read_text())
    if not request or any(not precondition(case)["valid"] for case in request):
        raise ValueError("invalid public request; no tested library imported")
    import shutil
    root = Path("/tmp/sqlfluff-public-api-files")
    if root.exists():
        shutil.rmtree(root)  # Fresh caller-owned fixture per process, never between persistent calls.
    sys.path.insert(0, str(args.runtime))
    from sqlfluff.core import Linter, FluffConfig
    from tools.fuzz_api_faults.lifecycle import snapshot
    primary = Linter(dialect="ansi")
    worker_start = snapshot("worker-start")
    results = []
    for case in request:
        config = FluffConfig(overrides={
            "dialect": case["options"]["dialect"], "rules": case["options"]["rules"],
            "templater": case["options"]["templater"],
        })
        flags_before = caller_flags(config)
        before = snapshot("before")
        events = []
        with capture_library_streams() as streams:
            try:
                value, effects = operation(case, primary, config, events)
                outcome = {"kind": "ok", "value": value}
                termination = {"kind": "return", "exit_code": 0}
            except Exception as error:
                outcome = {"kind": "error", "class": type(error).__module__ + "." + type(error).__qualname__,
                           "message": str(error)}
                effects = {}
                termination = {"kind": "exception", "exit_code": 0}
        after = snapshot("after")
        effects.update(streams)
        effects["public_call_events"] = events
        effects["caller_flags_before"] = flags_before
        effects["caller_flags_after"] = caller_flags(config)
        if case["operation"] == "file":
            effects["files"] = file_effects()
        results.append({
            "format": "optimbench-fuzz-observation-v1",
            "identity": {"task": TASK, "surface_id": case["surface_id"],
                         "case_sha256": case["case_sha256"], "build_sha256": args.build_sha256},
            "termination": termination, "outcome": outcome, "effects": effects,
            "diagnostics": {"execution_affinity": sorted(os.sched_getaffinity(0)),
                            "lifecycle": {"before": before, "after": after}},
        })
    data = {"worker": {"mode": "persistent", "target_library_in_process": True,
                       "worker_id": str(worker_start.get("pid")) + ":" +
                                    str(worker_start.get("sample_clock", {}).get("start_ns")),
                       "calls": len(results), "build_sha256": args.build_sha256},
            "observations": results}
    with os.fdopen(os.dup(args.report_fd), "w") as report:
        json.dump(data, report, ensure_ascii=True, sort_keys=True, allow_nan=False)
        report.write("\n")


if __name__ == "__main__":
    main()
