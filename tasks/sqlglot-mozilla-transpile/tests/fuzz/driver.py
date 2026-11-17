"""One-arm public API worker. JSON reporting uses the trusted standard library."""
import argparse
import contextlib
import gc
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import resource
import signal
import sys
import threading

from adapter import TASK, digest, precondition


def process_state():
    handlers = {}
    for number in signal.valid_signals():
        if number in (signal.SIGKILL, signal.SIGSTOP):
            continue
        handler = signal.getsignal(number)
        handlers[str(int(number))] = (
            f"{handler.__module__}.{handler.__qualname__}" if callable(handler) else handler)
    return {
        "threads": sorted(t.name for t in threading.enumerate()),
        "fd_count": len(os.listdir("/proc/self/fd")),
        "gc_enabled": gc.isenabled(), "gc_thresholds": list(gc.get_threshold()),
        "switch_interval": sys.getswitchinterval(), "recursion_limit": sys.getrecursionlimit(),
        "signal_handlers": handlers,
    }


def environment():
    # Never record credential/configuration values. A changed hash still detects
    # mutation, while a trusted per-worker path difference is not itself compared.
    return {key: hashlib.sha256(value.encode()).hexdigest() for key, value in os.environ.items()}


def environment_changes(before):
    after = environment()
    return {key: {"before_sha256": before.get(key), "after_sha256": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        self.rows.append({
            "logger": record.name, "level": record.levelname, "message": record.getMessage()})


def observe(case, sqlglot, build_sha256):
    stdout, stderr = io.StringIO(), io.StringIO()
    handler = Capture()
    logger = logging.getLogger("sqlglot")
    before = process_state()
    before_environment = environment()
    calls = []
    values = []
    callback_effects = []

    def call(name, operation):
        state = process_state()
        environment_before = environment()
        try:
            value = operation()
            return value
        finally:
            calls.append({
                "api": name, "before": state, "after": process_state(),
                "environment_changes": environment_changes(environment_before)})

    def invoke(sql):
        options = case["options"]
        read, write = options["read"], options["write"]
        parse_options = {"error_level": getattr(sqlglot.ErrorLevel, options["error_level"])}
        generate_options = {
            key: options[key] for key in ("pretty", "identify", "normalize", "comments")}
        generate_options["unsupported_level"] = getattr(
            sqlglot.ErrorLevel, options["unsupported_level"])
        surface = case["surface_id"]
        if surface == "sqlglot.transpile":
            return call(surface, lambda: sqlglot.transpile(
                sql, read=read, write=write, **parse_options, **generate_options))
        if surface == "sqlglot.parse":
            trees = call(surface, lambda: sqlglot.parse(sql, read=read, **parse_options))
            return [call("Expression.dump", tree.dump) if tree else None for tree in trees]
        if surface in ("sqlglot.tokenize", "sqlglot.tokens.Tokenizer.tokenize"):
            operation = (lambda: sqlglot.tokenize(sql, read=read)) if surface == "sqlglot.tokenize" else (
                lambda: sqlglot.Dialect.get_or_raise(read).tokenizer().tokenize(sql))
            tokens = call(surface, operation)
            return [{
                "type": token.token_type.name, "text": token.text, "line": token.line,
                "col": token.col, "start": token.start, "end": token.end,
                "comments": token.comments,
            } for token in tokens]
        if surface == "sqlglot.dialects.dialect.Dialect.parse":
            trees = call(surface, lambda: sqlglot.Dialect.get_or_raise(read).parse(sql, **parse_options))
            return [call("Expression.dump", tree.dump) if tree else None for tree in trees]
        if surface == "sqlglot.parser.Parser.parse":
            tokens = call("sqlglot.tokenize", lambda: sqlglot.tokenize(sql, read=read))
            parser = call("Dialect.parser", lambda: sqlglot.Dialect.get_or_raise(read).parser(**parse_options))
            trees = call(surface, lambda: parser.parse(tokens, sql))
            return [call("Expression.dump", tree.dump) if tree else None for tree in trees]
        if surface == "sqlglot.select":
            # Its SQL fragments still originate from the public SQL input.
            tree = call(surface, lambda: sqlglot.select(sql))
        else:
            tree = call("sqlglot.parse_one", lambda: sqlglot.parse_one(sql, read=read, **parse_options))
        if surface == "sqlglot.parse_one":
            return call("Expression.dump", tree.dump)
        callback_fault = case.get("callback_fault")
        if callback_fault is not None:
            events, yielded = [], []

            def callback(node):
                events.append(node.key)
                # Public mutation of a publicly parsed tree makes partial
                # callback effects observable even when traversal raises.
                node.add_comments(["verifier callback visited"])
                if len(events) == callback_fault["after"]:
                    raise OSError(callback_fault["errno"], "verifier public callback fault")
                return node if surface.endswith(".transform") else False

            try:
                if surface.endswith(".transform"):
                    changed = call(surface, lambda: tree.transform(
                        callback, copy=callback_fault["copy"]))
                    return {"changed": call("Expression.dump", changed.dump)}
                def walk():
                    for node in tree.walk(prune=callback):
                        yielded.append(node.key)
                    return yielded
                return call(surface, walk)
            finally:
                callback_effects.append({
                    "events": events, "yielded_before_error": yielded,
                    "original_after": call("Expression.dump", tree.dump),
                })
        if surface == "sqlglot.expressions.Expression.copy":
            original = call("Expression.dump", tree.dump)
            copied = call(surface, tree.copy)
            equal_before_mutation = copied == tree
            identifier = call("Expression.find", lambda: copied.find(sqlglot.exp.Identifier))
            if identifier is not None:
                call("Expression.set", lambda: identifier.set("this", "verifier_copy_identifier"))
            fresh = call("sqlglot.parse_one", lambda: sqlglot.parse_one(sql, read=read, **parse_options))
            return {
                "original": original, "copy": call("Expression.dump", copied.dump),
                "equal_before_mutation": equal_before_mutation, "distinct": copied is not tree,
                "original_after": call("Expression.dump", tree.dump),
                "fresh_independent": call("Expression.dump", fresh.dump),
            }
        if surface == "sqlglot.expressions.Expression.transform":
            before_dump = call("Expression.dump", tree.dump)
            changed = call(surface, lambda: tree.transform(
                lambda node: sqlglot.exp.Literal.number(7)
                if isinstance(node, sqlglot.exp.Literal) and node.is_number else node,
                copy=True))
            return {"before": before_dump, "after": call("Expression.dump", tree.dump),
                    "transformed": call("Expression.dump", changed.dump),
                    "sql": call("Expression.sql", lambda: changed.sql(dialect=write, **generate_options))}
        if surface == "sqlglot.expressions.Expression.walk":
            return call(surface, lambda: [node.key for node in tree.walk()])
        if surface == "sqlglot.expressions.Expression.find_all":
            return call(surface, lambda: [
                node.sql(dialect=write) for node in tree.find_all(sqlglot.exp.Column)])
        if surface == "sqlglot.dialects.dialect.Dialect.generate":
            return call(surface, lambda: sqlglot.Dialect.get_or_raise(write).generate(
                tree, **generate_options))
        return call("Expression.sql", lambda: tree.sql(dialect=write, **generate_options))

    logger.addHandler(handler)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                for step in case["sequence"]:
                    if step == "interleave":
                        values.append(invoke("SELECT 42 AS independent_value"))
                    elif step == "reinitialize":
                        # No undocumented private reset: construct a new documented parser.
                        call("Dialect.parser", lambda: sqlglot.Dialect.get_or_raise(
                            case["options"]["read"]).parser())
                    else:
                        values.append(invoke(case["input"]["sql"]))
                outcome = {"kind": "ok", "value": values}
                termination = {"kind": "return", "exit_code": 0}
            except Exception as error:
                outcome = {
                    "kind": "error", "class": type(error).__module__ + "." + type(error).__qualname__,
                    "message": str(error),
                }
                termination = {"kind": "exception", "exit_code": 0}
    finally:
        logger.removeHandler(handler)
    after = process_state()
    return {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {
            "task": TASK, "surface_id": case["surface_id"], "case_sha256": digest(case),
            "build_sha256": build_sha256,
        },
        "termination": termination, "outcome": outcome,
        "effects": {
            "stdout_utf8": stdout.getvalue(), "stderr_utf8": stderr.getvalue(),
            "log_messages": handler.rows, "process_before": before,
            "process_after": after, "per_call_lifecycle": calls,
            "completed_values": values,
            "callback_faults": callback_effects,
            "environment_changes": environment_changes(before_environment),
        },
        "diagnostics": {
            "memory": {"metric": "getrusage.ru_maxrss", "unit": "KiB",
                       "scope": "fresh-worker-lifetime-including-startup",
                       "peak": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       "mode": "report-only", "verdict_impact": False},
            "library_path": sqlglot.__file__,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.build_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.build_sha256):
        parser.error("Build SHA-256 must be a lowercase digest")
    case = json.loads(args.case.read_text())
    surface_map = json.loads(Path(__file__).with_name("surface.json").read_text())
    surface = next((x for x in surface_map["surfaces"] if x["id"] == case.get("surface_id")), {})
    admitted = precondition(case, surface)
    if not admitted["valid"]:
        raise ValueError("Out-of-contract case rejected before library import: " + admitted["reason"])
    sys.path.insert(0, str(args.runtime.resolve()))
    import_stdout, import_stderr = io.StringIO(), io.StringIO()
    import_before = process_state()
    import_environment_before = environment()
    import_capture = Capture()
    logger = logging.getLogger("sqlglot")
    logger.addHandler(import_capture)
    try:
        with contextlib.redirect_stdout(import_stdout), contextlib.redirect_stderr(import_stderr):
            try:
                import sqlglot
            except Exception as error:
                result = {
                    "format": "optimbench-fuzz-observation-v1",
                    "identity": {"task": TASK, "surface_id": case["surface_id"],
                                 "case_sha256": digest(case), "build_sha256": args.build_sha256},
                    "termination": {"kind": "exception", "exit_code": 0},
                    "outcome": {"kind": "error",
                                "class": type(error).__module__ + "." + type(error).__qualname__,
                                "message": str(error)},
                    "effects": {}, "diagnostics": {"initialization_failed": True},
                }
                sqlglot = None
    finally:
        logger.removeHandler(import_capture)
    import_after = process_state()
    if sqlglot is not None:
        if not Path(sqlglot.__file__).resolve().is_relative_to(args.runtime.resolve()):
            raise RuntimeError("Protected runtime path did not load")
        result = observe(case, sqlglot, args.build_sha256)
    result["effects"]["import_effects"] = {
        "stdout_utf8": import_stdout.getvalue(), "stderr_utf8": import_stderr.getvalue(),
        "log_messages": import_capture.rows, "process_before": import_before,
        "process_after": import_after,
        "environment_changes": environment_changes(import_environment_before),
    }
    # Independent serializer: no candidate/library function is used to report.
    result["diagnostics"]["execution_affinity"] = sorted(os.sched_getaffinity(0))
    args.output.write_text(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
