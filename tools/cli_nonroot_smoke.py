"""Run a task's healthy CLI cases on pristine exactly as cli_faults does, before a VM judge run.

Each CLI_CASES case runs once through tools.fuzz_runners.cli_faults.run_pair with
faults=("healthy",): uid 10000, --cap-drop=ALL, no-new-privileges, read-only
root. cli_gate rejects the whole gate when a pristine healthy case exits nonzero,
so a failure here is a guaranteed wasted judge run. This does not qualify the
fault matrix, timing, or candidate behavior.

Needs the task image (or a stand-in with the same layout and non-root
constraints) and a pristine build directory, e.g. the image's /app/baseline-build.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(task, image, build, out, public_inputs=None, projects=None):
    sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools/runner")]
    from judge import fuzzing_io as io
    from judge.fuzzing_generic import task_runners
    from tools.fuzz_runners.cli_faults import run_pair
    from runner import Step

    task, out = Path(task).resolve(), Path(out).resolve()
    _, hooks = task_runners(task)
    context = {"task": task, "out": out / "requests", "hooks": hooks, "builds": {},
               "public_inputs": public_inputs, "projects": projects}
    context["out"].mkdir(parents=True)
    value = io.request(context, "CLI_CASES", "judge-generic-cli-cases-v1")
    if value is None or not value["applicable"]:
        return {"task": task.name, "status": "not_applicable", "cases": []}
    rows = []
    for index, spec in enumerate(value["cases"]):
        step = Step(**spec["step"]) if isinstance(spec["step"], dict) else spec["step"]
        raw = run_pair(step=step, inputs=spec["inputs"], baseline=build, candidate=build, image=image,
                       out=out / f"case-{index}", watch_inputs=spec["watch_inputs"],
                       watch_outputs=spec.get("watch_outputs", ()), output_dirs=spec.get("output_dirs", ()),
                       required_outputs=spec.get("required_outputs", ()), faults=("healthy",),
                       case_id=f"nonroot-smoke-{index}", payload_class=spec["payload_class"],
                       task=task, public_inputs=public_inputs, projects=projects)
        row = raw["observations"]["healthy"]["baseline"]
        stderr = b""
        try:
            stderr = io.stream_bytes(row["stderr"], out / f"case-{index}")[:2048]
        except (KeyError, ValueError, OSError):
            pass
        rows.append({"case": index, "payload_class": spec["payload_class"], "argv": step.argv,
                     "status": row.get("status"), "uid": row.get("uid"), "exit_code": row.get("exit_code"),
                     "reason": row.get("reason"), "stderr_head": stderr.decode(errors="replace")})
    passed = all(r["status"] == "observed" and r["exit_code"] == 0 and r["uid"] not in (None, 0) for r in rows)
    return {"task": task.name, "status": "passed" if passed else "failed", "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--image", required=True, help="immutable repo@sha256 or local sha256 image id")
    parser.add_argument("--build", required=True, help="pristine build directory mounted as /app/build")
    parser.add_argument("--out")
    parser.add_argument("--public-inputs")
    args = parser.parse_args()
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="cli-nonroot-smoke-")) / "run"
    result = run(args.task, args.image, Path(args.build).resolve(), out, args.public_inputs)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("passed", "not_applicable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
