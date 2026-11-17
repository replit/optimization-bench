"""Read, execute and materialize each distinct request once before process exit."""
import json
from pathlib import Path
import sys

# This protected directory has no sqlglot package. Candidate resolution remains
# the adapter's PYTHONPATH, while helper imports cannot select installed namesakes.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis_lineage import run_operation
from analysis_diff import run

operations = {"lineage": run_operation, "diff": run}
root = Path(sys.argv[2])
rows = json.loads((root / "cases.json").read_text())
for row in rows:
    request = json.loads((root / row["sha256"]).read_text())
    result = operations[row["family"]]([request])
    (root / (row["path"] + ".out")).write_bytes(result)
