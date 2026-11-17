"""Host-side reference smoke runner using the shared DockerExecutor.

No task/candidate qualification is implied. The default Docker sandbox retains
UID10000, no network, dropped capabilities, no-new-privileges and pids/memory
bounds. Kernel-unsupported sanitizers are recorded, not retried privileged.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "runner"
sys.path.insert(0, str(RUNNER))
from runner import Context, HarnessError, Step
from docker_executor import DockerExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["native", "python", "javascript", "go", "rust"], required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpuset")
    parser.add_argument("--extended-rust", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("use a fresh output directory")
    out = args.out.resolve()
    out.mkdir(parents=True)
    build, scratch = out / "build", out / "scratch"
    build.mkdir()
    scratch.mkdir()
    ctx = Context(scratch=scratch, build=build, inputs=build, projects=build, workload=build, docker=True)
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    executor = DockerExecutor(image_id, cpuset=args.cpuset,
                              extra_docker_flags=["-v", f"{HERE}:/runner-tools:ro"])
    try:
        results = executor.run([
            Step(["python3", "/runner-tools/probe.py", "--language", args.language, "--out", "/scratch/probe"]
                 + (["--extended-rust"] if args.extended_rust else []),
                 timeout=1800)], ctx, {})
        if results[0].exit_code != 0:
            raise HarnessError("probe controller failed: " + results[0].stderr.decode(errors="replace"))
        data = json.loads((scratch / "probe/capabilities.json").read_text())
        data["runner_image"] = image_id
        data["execution_protocol"] = "shared-DockerExecutor"
        data["container_leftovers"] = results[0].leftovers
        data["container_telemetry"] = results[0].telemetry
    except (HarnessError, OSError, ValueError) as exc:
        data = {"schema": "optimbench-runner-capabilities-v1", "language": args.language,
                "runner_image": image_id, "status": "invalid_run", "reason": str(exc),
                "candidate_qualification": False}
    (out / "capabilities.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data.get("capabilities", data), indent=2))
    return 2 if data.get("status") == "invalid_run" else 0


if __name__ == "__main__":
    raise SystemExit(main())
