#!/usr/bin/env python3
"""Task-owned untimed arm driver; JSON report uses a separate inherited FD."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import threading

from adapter import TASK, precondition


@contextmanager
def capture_library_streams():
    """Capture Python/native FD writes without mixing them into the report FD."""
    result = {}
    sys.stdout.flush()
    sys.stderr.flush()
    original_out, original_err = os.dup(1), os.dup(2)
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            os.dup2(stdout.fileno(), 1)
            os.dup2(stderr.fileno(), 2)
            try:
                yield result
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(original_out, 1)
                os.dup2(original_err, 2)
                stdout.seek(0)
                stderr.seek(0)
                result.update(library_stdout_base64=base64.b64encode(stdout.read()).decode(),
                              library_stderr_base64=base64.b64encode(stderr.read()).decode())
    finally:
        os.dup2(original_out, 1)
        os.dup2(original_err, 2)
        os.close(original_out)
        os.close(original_err)


def lifecycle():
    root = Path("/proc/self")
    try:
        fds = {p.name: os.readlink(p) for p in (root / "fd").iterdir() if p.exists()}
    except (OSError, FileNotFoundError):
        fds = None
    child_file = root / "task" / str(os.getpid()) / "children"
    try:
        children = [int(v) for v in child_file.read_text().split()]
    except OSError:
        children = None  # Unknown is not a successful zero-worker observation.
    return {"fd_targets": fds, "python_thread_ids": sorted(t.ident for t in threading.enumerate()
                                                        if t.ident is not None),
            "direct_child_ids": children}


def cli(case):
    op = case["operation"]
    command = "lint" if op == "cli_stdin" else op.removeprefix("cli_")
    with tempfile.TemporaryDirectory(prefix="sqlfluff-fuzz-") as directory:
        root = Path(directory)
        work = root / "work"
        home = root / "home"
        tmp = root / "tmp"
        for folder in (work, home, tmp):
            folder.mkdir()
        sql = case["input"]["sql"]
        if op != "cli_stdin":
            (work / "case.sql").write_text(sql, encoding="utf-8")
        argv = [sys.executable, "-m", "sqlfluff", command, "-" if op == "cli_stdin" else "case.sql",
                "--dialect", case["options"]["dialect"], "--nocolor"]
        if command in ("lint", "fix"):
            argv += ["--disable-progress-bar", "-p", str(case["options"]["processes"])]
        if command in ("lint", "parse"):
            argv += ["--format", "json"]
        env = os.environ.copy()
        env.update(HOME=str(home), TMPDIR=str(tmp), PYTHONHASHSEED="0",
                   PYTHONDONTWRITEBYTECODE="1", LC_ALL="C.UTF-8", TZ="UTC",
                   PYTHONWARNINGS="ignore::UserWarning:multiprocessing.resource_tracker")
        result = subprocess.run(argv, input=sql.encode() if op == "cli_stdin" else None,
                                cwd=work, env=env, capture_output=True, timeout=20)
        stdout = result.stdout
        encoding = "base64-raw"
        if command == "lint":
            # The task excludes only per-file top-level timings. Never nested
            # timing-like values, errors, partial output, statistics or ordering.
            try:
                records = json.loads(stdout)
                if isinstance(records, list) and all(isinstance(r, dict) for r in records):
                    stdout = json.dumps([{k: v for k, v in r.items() if k != "timings"}
                                         for r in records], ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()
                    encoding = "base64-canonical-top-level-timings-only"
            except (ValueError, UnicodeError):
                pass
        files = {}
        for path in sorted(work.rglob("*")):
            if path.is_symlink():
                files[path.relative_to(work).as_posix()] = {
                    "kind": "symlink", "target": os.readlink(path),
                }
            elif path.is_file():
                files[path.relative_to(work).as_posix()] = {
                    "kind": "file",
                    "bytes_base64": base64.b64encode(path.read_bytes()).decode(),
                    "mode": path.stat().st_mode & 0o777,
                }
            elif path.is_dir():
                files[path.relative_to(work).as_posix()] = {
                    "kind": "directory", "mode": path.stat().st_mode & 0o777,
                }
            else:
                files[path.relative_to(work).as_posix()] = {
                    "kind": "special", "mode": path.lstat().st_mode,
                }
        return {"exit_code": result.returncode,
                "stdout_encoding": encoding,
                "stdout_base64": base64.b64encode(stdout).decode(),
                "stderr_encoding": "base64-raw",
                "stderr_base64": base64.b64encode(result.stderr).decode(),
                "files": files}


def operation(case):
    op = case["operation"]
    if op.startswith("cli_"):
        return None, cli(case)
    import sqlfluff
    if op in ("api_lint", "api_fix", "api_parse"):
        return getattr(sqlfluff, op.removeprefix("api_"))(
            case["input"]["sql"], dialect=case["options"]["dialect"]), {}
    if op.startswith("api_list"):
        return getattr(sqlfluff, op.removeprefix("api_"))(), {}
    if op == "block_config_state":
        from sqlfluff.utils.reflow.config import ReflowConfig
        config = ReflowConfig.from_dict({})
        transcript = []
        for _ in range(3):
            first = config.get_block_config({"comma"})
            first.incorporate(before="touch")
            transcript.append({
                "later_same": config.get_block_config({"comma"}).spacing_before,
                "other": config.get_block_config({"keyword"}).spacing_before,
                "fresh": ReflowConfig.from_dict({}).get_block_config({"comma"}).spacing_before,
                "same_again": config.get_block_config({"comma"}).spacing_before,
            })
        return transcript, {}
    if op == "frozen_match_result":
        from sqlfluff.core.parser.match_result import MatchResult
        empty = MatchResult.empty_at(0)
        appended = empty.append(MatchResult(slice(0, 1)))
        # Do not compare object identity or mutate the frozen result's dict.
        return {"initial_length": len(empty), "appended_length": len(appended),
                "later_empty_length": len(MatchResult.empty_at(0)),
                "original_after_append": empty.stringify()}, {}
    if op == "dialect_state":
        from sqlfluff.core.dialects import dialect_selector
        dialect = case["options"]["dialect"]
        marker = "FUZZ_DIALECT_OWNERSHIP_MARKER"
        first = dialect_selector(dialect)
        first.sets("reserved_keywords").add(marker)
        other = "postgres" if dialect != "postgres" else "ansi"
        return {"same": [marker in dialect_selector(dialect).sets("reserved_keywords") for _ in range(3)],
                "other": marker in dialect_selector(other).sets("reserved_keywords")}, {}
    raise ValueError("unknown precondition-validated operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--report-fd", type=int, required=True)
    args = parser.parse_args()
    if args.report_fd in (0, 1, 2):
        raise ValueError("report must use a separate inherited private FD")
    case = json.loads(args.case.read_text())
    valid = precondition(case, None)
    if not valid["valid"]:
        raise ValueError("unsupported case precondition: " + valid["reason"])
    sys.path.insert(0, str(args.runtime.resolve()))
    os.environ["PYTHONPATH"] = str(args.runtime.resolve())
    before = lifecycle()
    with capture_library_streams() as streams:
        try:
            value, effects = operation(case)
            outcome = {"kind": "ok", "value": value}
            termination = {"kind": "return", "exit_code": 0}
            if case["operation"].startswith("cli_"):
                termination["exit_code"] = effects["exit_code"]
                if effects["exit_code"]:
                    outcome = {"kind": "error", "class": "CLIExit",
                               "category": "nonzero-exit-with-complete-streams",
                               "value": None}
        except subprocess.TimeoutExpired as error:
            outcome = {"kind": "error", "class": type(error).__name__, "message": str(error)}
            effects = {"partial_stdout_base64": base64.b64encode(error.stdout or b"").decode(),
                       "partial_stderr_base64": base64.b64encode(error.stderr or b"").decode()}
            termination = {"kind": "timeout", "exit_code": None}
        except BaseException as error:
            outcome = {"kind": "error", "class": type(error).__name__, "message": str(error)}
            effects = {}
            termination = {"kind": "exception", "exit_code": 0}
    effects.update(streams)
    after = lifecycle()
    report = {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {"task": TASK, "surface_id": case["surface_id"],
                     "case_sha256": case["case_sha256"], "build_sha256": args.build_sha256},
        "termination": termination, "outcome": outcome, "effects": effects,
        "diagnostics": {
            "execution_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "lifecycle": {"before": before, "after": after},
            "memory": {"metric": "getrusage(RUSAGE_SELF).ru_maxrss",
                       "native_value": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       "unit": "KiB-on-Linux", "scope": "driver-only; excludes subprocess peak",
                       "verdict_impact": False},
        },
    }
    # Independent stdlib reporter, never SQLFluff's JSON/CLI serialization.
    payload = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    with os.fdopen(os.dup(args.report_fd), "wb") as stream:
        stream.write(payload)


if __name__ == "__main__":
    main()
