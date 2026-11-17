#!/usr/bin/env python3
"""Two fresh pristine workers per case using the protected shared executor.

This is adapter feasibility evidence, not a candidate verdict or full campaign.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import uuid

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools/runner"))
from runner import Context, Step
from docker_executor import DockerExecutor
from judge.verifier import build_inventory
if __package__:
    from . import adapter
else:
    import adapter


def run_case(case, build, image, cpuset, worker_python, monitor_file=None):
    with tempfile.TemporaryDirectory(prefix="black-fuzz-smoke-") as directory:
        scratch = Path(directory)
        (scratch / "case.json").write_text(json.dumps(case, ensure_ascii=False))
        ctx = Context(scratch, build, scratch, scratch, OUT, docker=True)
        flags = ["--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "-v", f"{OUT}:/verifier-fuzz:ro"]
        if monitor_file:
            flags.extend(["-v", f"{Path(monitor_file).resolve()}:/verifier-monitor.py:ro"])
        executor = DockerExecutor(
            image, cpuset, memory="16g",
            extra_docker_flags=flags,
            tmpfs="/tmp:rw,nosuid,nodev,size=512m")
        command = [worker_python, "/verifier-fuzz/worker.py", "--case", "/scratch/case.json",
                   "--runtime", "/app/build/runtime", "--build-sha256", build_inventory(build)]
        if monitor_file:
            command.extend(["--monitor", "/verifier-monitor.py"])
        worker_id = uuid.uuid4().hex
        result = executor.run([Step(command, timeout=20)], ctx, {
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
            "OPTIMBENCH_FUZZ_WORKER_ID": worker_id,
            "HOME": "/scratch", "TZ": "UTC", "LC_ALL": "C.UTF-8"})[0]
        if result.exit_code or result.leftovers:
            raise RuntimeError(json.dumps({
                "kind": "pristine-worker-infrastructure-failure",
                "exit_code": result.exit_code, "leftovers": result.leftovers,
                "stdout": result.stdout.decode(errors="replace"),
                "stderr": result.stderr.decode(errors="replace")}))
        observation = json.loads(result.stdout)
        assert observation["diagnostics"]["compiled"], "Native mypyc import not confirmed"
        assert observation["diagnostics"]["library_origin"].startswith("/app/build/runtime/")
        assert observation["diagnostics"]["library_origin"].endswith(".so")
        assert observation["diagnostics"]["worker_instance_id"] == worker_id
        return observation, {"worker_stderr": result.stderr.decode(errors="replace"),
                             "leftovers": result.leftovers, "worker_instance_id": worker_id,
                             "isolation": "fresh DockerExecutor.run container per call",
                             "shared_executor_sha256": hashlib.sha256(
                                 (REPO / "tools/runner/docker_executor.py").read_bytes()).hexdigest()}


def main(args):
    args.out.mkdir(parents=True, exist_ok=False)
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    cases = list(adapter.generate(random.Random(1729), args.cases))
    report = {
        "format": "optimbench-fuzz-v1", "purpose": "pristine-adapter-native-smoke-only",
        "qualification": "undertested", "candidate_campaign": False,
        "task": "black-project-format", "image": image,
        "build_sha256": build_inventory(args.build), "cpuset": args.cpuset,
        "uid": 10000, "network": "none", "seed": 1729, "cases": [],
        "worker_python": args.worker_python,
        "harness_files_sha256": {
            name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
            for name in ("adapter.py", "worker.py", "smoke.py", "adapter.json", "corpus/manifest.json")
        },
    }
    for index, case in enumerate(cases):
        left, left_transport = run_case(case, args.build, image, args.cpuset, args.worker_python)
        right, right_transport = run_case(case, args.build, image, args.cpuset, args.worker_python)
        comparison = adapter.compare(left, right, case)
        row = {"index": index, "case_sha256": adapter.case_sha256(case),
               "surface_id": case["surface_id"], "comparison": comparison,
               "baseline_transport": left_transport, "replay_transport": right_transport}
        report["cases"].append(row)
        (args.out / "smoke.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if index == 0 or not comparison["equal"]:
            (args.out / f"case-{index}.json").write_text(json.dumps(case, indent=2) + "\n")
            (args.out / f"observation-{index}-a.json").write_text(json.dumps(left, indent=2) + "\n")
            (args.out / f"observation-{index}-b.json").write_text(json.dumps(right, indent=2) + "\n")
        if not comparison["equal"]:
            raise RuntimeError("Pristine self-replay differs; adapter is blocked, not candidate regression")
        print(f"pristine self-replay {index}: {case['surface_id']} matched", flush=True)
    report["pristine_replay_passed"] = True
    assert report["harness_files_sha256"] == {
        name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
        for name in report["harness_files_sha256"]
    }, "Harness changed during pristine self-replay"
    report["cases_completed"] = len(cases)
    report["distinct_surfaces"] = sorted({case["surface_id"] for case in cases})
    (args.out / "smoke.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(cases), "fresh_workers": 2 * len(cases),
                      "qualification": "undertested", "pristine_replay_passed": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", default="0-7")
    parser.add_argument("--cases", type=int, default=18)
    parser.add_argument("--worker-python", default="python3")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.build = args.build.resolve()
    main(args)
