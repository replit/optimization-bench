#!/usr/bin/env python3
"""Run the fixed compiler through ccache after generated file mtimes settle."""
import os
from pathlib import Path
import sys
import time

# mypyc 1.20.2 deliberately sets generated C/header mtimes one second ahead.
# ccache correctly refuses such inputs and otherwise compiles them twice (once
# speculatively, then uncached). Wait rather than weaken ccache's validity checks
# or modify compiler flags, generated content, or timestamps.
root = Path(os.environ["HATCH_MYPYC_BUILD_DIR"]) / "build"
newest = max((p.stat().st_mtime for p in root.rglob("*") if p.is_file()),
             default=0)
delay = newest - time.time() + 0.05
if delay > 0:
    time.sleep(delay)
os.execvp("ccache", ["ccache", "gcc", *sys.argv[1:]])
