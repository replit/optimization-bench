"""Image-build gate: every public commitment, including literal historical errors."""
import importlib.util
from pathlib import Path
import sys
from runner import Context, LocalExecutor, fingerprint, run_case
import tempfile

workload = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("task", workload / "task.py")
task = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task)
inputs = workload / "inputs"
expected = {doc["path"]: (doc["expect_len"], doc["expect_sha"])
            for doc in task.references(inputs)}
actual = {}
for case in task.cases(inputs):
    with tempfile.TemporaryDirectory(prefix="baseline-gate-") as temporary:
        ctx = Context(Path(temporary), Path(sys.argv[1]), inputs, Path("/app/projects"), workload)
        outputs, _ = run_case(task, case, ctx, LocalExecutor())
        actual.update({name: fingerprint(data) for name, data in outputs.items()})
if actual != expected:
    bad = sorted(key for key in actual.keys() | expected.keys() if actual.get(key) != expected.get(key))
    sys.exit("Public baseline output mismatch: " + ", ".join(bad))
print(f"Public baseline gate: {len(actual)} exact commitments")
