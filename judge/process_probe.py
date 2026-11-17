"""Fresh-process Python global state after a representative public library call."""
import gc
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading
try:
    from .probe_lifecycle import finish_multiprocessing
except ImportError:
    from probe_lifecycle import finish_multiprocessing


def handler(value):
    if isinstance(value, int):
        return int(value)
    return getattr(value, "__module__", "") + "." + getattr(value, "__qualname__", type(value).__name__)


def snapshot():
    signals = {}
    for number in signal.valid_signals():
        try:
            signals[str(int(number))] = handler(signal.getsignal(number))
        except (OSError, ValueError):
            continue
    # CPython can start with frozen objects; a boolean would hide another freeze.
    return {"gc_thresholds": gc.get_threshold(), "gc_enabled": gc.isenabled(),
            "gc_frozen": gc.get_freeze_count(), "switch_interval": sys.getswitchinterval(),
            "recursion_limit": sys.getrecursionlimit(), "environment": dict(os.environ),
            "signal_handlers": signals, "threads": len(os.listdir("/proc/self/task")),
            "python_threads": len(threading.enumerate())}


def main():
    task = sys.argv[1]
    configured = len(sys.argv) in (4, 5)
    if len(sys.argv) == 5:
        sys.path.insert(0, sys.argv[4])
    elif not configured:
        sys.path.insert(0, "/app/build/site" if task == "numpy-quantile" else "/app/build/runtime")
    observations = {}
    # Installed before importing candidate code, only in the untimed observer.
    coverage_id = (Path("/scratch/probe-id").read_text() if Path("/scratch/probe-id").is_file()
                   else os.environ.get("OPTIMBENCH_COVERAGE_ID", ""))
    collector = None
    if coverage_id:
        try:
            from .execution_coverage import PythonCollector
        except ImportError:
            from execution_coverage import PythonCollector
        collector = PythonCollector(os.environ.get("OPTIMBENCH_COVERAGE_ROOT", "/app/build"), coverage_id)
        collector.start()
    if configured:
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
        operation = load("verifier_representative", sys.argv[3])
        run = getattr(operation, "run", None)
        if not callable(run):
            Path("/scratch/process-state.json").write_text(json.dumps({
                "status": "not_configured", "reason": "Representative script exports no run function"}))
            return
        module = importlib.import_module(sys.argv[2]) if len(sys.argv) == 5 else load(
            "representative_library", sys.argv[2])
        run(module)
    elif task == "sqlfluff-project-lint":
        import sqlfluff
        from sqlfluff.core import Linter, FluffConfig
        from sqlfluff.core.linter.runner import MultiProcessRunner
        observations["pool_start_method"] = MultiProcessRunner.POOL_TYPE.__self__.get_start_method()
        folder = Path("/scratch/sql");folder.mkdir()
        for n in range(2):
            (folder / (str(n) + ".sql")).write_text("SELECT 1;\n")
        linter = Linter(config=FluffConfig(overrides={"dialect": "ansi", "processes": 2}))
        linter.lint_paths((str(folder),), processes=2)
    elif task == "black-project-format":
        import black
        black.format_str("x=1\n", mode=black.Mode())
    elif task == "sqlglot-mozilla-transpile":
        import sqlglot
        sqlglot.transpile("SELECT 1 + 2", read="postgres", write="sqlite")
    elif task == "numpy-quantile":
        import numpy as np
        np.quantile(np.arange(100, dtype=float), [0.25, 0.5, 0.75])
    else:
        Path("/scratch/process-state.json").write_text(json.dumps({
            "status": "not_configured", "reason": "No representative Python operation"}))
        return
    if collector is not None:
        evidence = collector.stop()
        Path("/scratch/python-coverage.json").write_text(json.dumps(evidence, sort_keys=True))
    observations.update(snapshot())
    Path("/scratch/process-state.json").write_text(json.dumps(observations, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    finally:
        # Observe candidate post-call state before normal interpreter cleanup.
        finish_multiprocessing()
