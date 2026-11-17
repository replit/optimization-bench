"""Offline CLI fixture, never a real XML parser or a production task payload."""
import json
import os
from pathlib import Path
import resource
import signal
import sys

row = json.loads(Path(__file__).with_name("outcome.json").read_text())
sys.stdout.write(row["stdout"])
sys.stderr.write(row["stderr"])
sys.stdout.flush()
sys.stderr.flush()
if row["code"] < 0:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.kill(os.getpid(), signal.Signals(-row["code"]))
raise SystemExit(row["code"])
