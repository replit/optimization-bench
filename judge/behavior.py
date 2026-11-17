"""Compose hidden correctness and process-state layers before timing or review."""
import json
from pathlib import Path
import tempfile
from . import differential, process_state


def check(task, baseline, candidate, image, cpuset, output=None, public_inputs=None, projects=None):
    settings = json.loads((Path(task) / "tests/judge.json").read_text())
    output = Path(output) if output else Path(tempfile.mkdtemp(prefix="judge-behavior-"))
    try:
        hidden = differential.check(task, baseline, candidate, image, cpuset, output / "differential")
    except differential.DifferentialMismatch as error:
        return {"status": "differential_mismatch", "correct": False, "claimable": False,
                "speedup": None, "differential": error.report, "evidence_directory": str(output)}
    state = process_state.check(task, baseline, candidate, image, cpuset, public_inputs, projects) \
        if settings.get("process_state", {}).get("enabled") else {
            "passed": False, "status": "not_configured", "coverage": "not_configured"}
    output.mkdir(parents=True, exist_ok=True)
    (output / "process-state.json").write_text(json.dumps(state, indent=2) + "\n")
    result = {"differential": hidden, "process_state": state, "evidence_directory": str(output)}
    if not state["passed"]:
        result.update(status="needs_review", correct=None, claimable=False, speedup=None)
    return result
