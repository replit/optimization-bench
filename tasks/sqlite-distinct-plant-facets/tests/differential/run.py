"""Compile the observer against the selected build's own SQLite and run one case."""
import json
from pathlib import Path
import subprocess
import sys

case = json.loads(Path(sys.argv[1]).read_text())
build = Path("/app/build")
objects = build / "objects"
# decimal.c is the build's own ext/misc copy, linked as the shell and dec driver do.
compiled = subprocess.run(
    ["cc", "-O2", "-DSQLITE_CORE", "-I" + str(objects), "-I" + str(build / "source/src"),
     "/verifier-differential/probe.c", str(build / "source/ext/misc/decimal.c"),
     str(objects / "libsqlite3.a"), "-lm", "-ldl", "-lpthread", "-o", "/scratch/probe"],
    capture_output=True)
if compiled.returncode:
    sys.stderr.buffer.write(compiled.stderr)
    raise SystemExit(2)
script = Path("/scratch/case.sql")
script.write_bytes(b"\0".join(statement.encode() for statement in case["statements"]))
result = subprocess.run(["/scratch/probe", "/scratch/case.db", str(script)],
                        capture_output=True, timeout=25)
try:
    steps = json.loads(result.stdout)
except (ValueError, UnicodeDecodeError):
    steps = None
print(json.dumps({"exit_code": result.returncode, "steps": steps,
                  "stdout_hex": None if steps is not None else result.stdout.hex(),
                  "stderr_hex": result.stderr.hex()}, sort_keys=True))
if result.returncode or steps is None:
    raise SystemExit(1)
