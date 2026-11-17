"""Frozen pre-hook Python probe, used only for offline compatibility tests."""
import gc
import importlib
import json
import os
from pathlib import Path
import signal
import sys
import threading


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
    return {"gc_thresholds": gc.get_threshold(), "gc_enabled": gc.isenabled(),
            "gc_frozen": gc.get_freeze_count() > 0, "switch_interval": sys.getswitchinterval(),
            "recursion_limit": sys.getrecursionlimit(), "environment": dict(os.environ),
            "signal_handlers": signals, "threads": len(os.listdir("/proc/self/task")),
            "python_threads": len(threading.enumerate())}


def main():
    task = sys.argv[1]
    sys.path.insert(0, "/app/build/site" if task == "numpy-quantile" else "/app/build/runtime")
    observations = {}
    if task == "sqlfluff-project-lint":
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
    observations.update(snapshot())
    Path("/scratch/process-state.json").write_text(json.dumps(observations, sort_keys=True))


if __name__ == "__main__":
    main()
