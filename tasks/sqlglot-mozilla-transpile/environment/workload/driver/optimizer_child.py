"""One cold batch. All imports, API calls and result writes precede child exit."""
import importlib.util
import json
from pathlib import Path
import sys

# Candidate import resolution is supplied by the same protected adapter as the
# original child. Load only this helper by path, never an installed namesake.
spec = importlib.util.spec_from_file_location(
    "protected_optimizer", Path(__file__).with_name("optimizer.py"))
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)

root = Path(sys.argv[2])
rows = json.loads((root / "cases.json").read_text())
for row in rows:
    request = json.loads((root / row["sha256"]).read_text())
    (root / (row["path"] + ".out")).write_bytes(optimizer.run_case(request))
