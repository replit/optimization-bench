"""Run a frozen two-arm header matrix in separate shared-executor workers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .header_matrix import compare_cells, validate_manifest
from .receipt import digest, image, sha256

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "runner"))
from runner import Context, HarnessError, Step
from docker_executor import DockerExecutor


def execute_arm(image_ref, build, manifest, out, *, cpuset=None):
    out.mkdir(parents=True)
    ctx = Context(scratch=out, build=build.resolve(), inputs=build.resolve(),
                  projects=build.resolve(), workload=build.resolve(), docker=True)
    executor = DockerExecutor(image_ref, cpuset=cpuset, extra_docker_flags=[
        "-v", f"{HERE}:/runner-tools:ro", "-v", f"{manifest.resolve()}:/header-manifest.json:ro"])
    # Compile/link are deliberately untimed. Shared execution telemetry is
    # retained as diagnostic evidence, not fed to a performance score.
    results = executor.run([Step([
        "python3", "/runner-tools/header_matrix.py", "--manifest", "/header-manifest.json",
        "--build", "/app/build", "--out", "/scratch/matrix"],
        timeout=120 * (2 + 5 * len(json.loads(manifest.read_text())["cells"])))], ctx, {})
    if results[0].exit_code != 0:
        raise HarnessError("header matrix worker failed: " + results[0].stderr.decode(errors="replace"))
    data = json.loads((out / "matrix/matrix.json").read_text())
    data["execution_protocol"] = "shared-DockerExecutor"
    data["leftovers"] = results[0].leftovers
    return data


def run_pair(*, image_ref, parent_image, baseline, candidate, manifest, out,
             baseline_source_sha256, candidate_source_sha256,
             baseline_build_sha256, candidate_build_sha256, cpuset=None):
    image(image_ref)
    image(parent_image)
    for value in (baseline_source_sha256, candidate_source_sha256,
                  baseline_build_sha256, candidate_build_sha256):
        digest(value)
    validate_manifest(json.loads(manifest.read_text()))
    if out.exists():
        raise ValueError("use a fresh output directory")
    out.mkdir(parents=True)
    # Resolve once: never let a mutable local tag select different arm images.
    image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image_ref], text=True).strip()
    baseline_data = execute_arm(image_id, baseline, manifest, out / "baseline", cpuset=cpuset)
    candidate_data = execute_arm(image_id, candidate, manifest, out / "candidate", cpuset=cpuset)
    if baseline_data["manifest_sha256"] != candidate_data["manifest_sha256"]:
        raise HarnessError("header manifest changed between arms")
    baseline_cells = {c["id"]: c for c in baseline_data["cells"]}
    candidate_cells = {c["id"]: c for c in candidate_data["cells"]}
    result = {
        "schema": "optimbench-header-consumer-matrix-v1", "runner_image": image_id,
        "parent_task_image": parent_image, "manifest_sha256": sha256(manifest),
        "source_sha256": {"baseline": baseline_source_sha256, "candidate": candidate_source_sha256},
        "build_sha256": {"baseline": baseline_build_sha256, "candidate": candidate_build_sha256},
        "binding_validation": "source/build commitments supplied by controller; exact consumed header/library/TU hashes recorded",
        "baseline": baseline_data, "candidate": candidate_data,
        "cells": [compare_cells(baseline_cells[c], candidate_cells[c]) for c in baseline_cells],
        "qualification": "not-a-task-verdict",
    }
    (out / "header_consumer_matrix.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "parent-image", "baseline-source-sha256", "candidate-source-sha256",
                 "baseline-build-sha256", "candidate-build-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("baseline-build", "candidate-build", "manifest", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cpuset")
    args = parser.parse_args()
    result = run_pair(
        image_ref=args.image, parent_image=args.parent_image,
        baseline=args.baseline_build, candidate=args.candidate_build,
        manifest=args.manifest, out=args.out, cpuset=args.cpuset,
        baseline_source_sha256=args.baseline_source_sha256,
        candidate_source_sha256=args.candidate_source_sha256,
        baseline_build_sha256=args.baseline_build_sha256,
        candidate_build_sha256=args.candidate_build_sha256)
    print(json.dumps(result["cells"], indent=2))


if __name__ == "__main__":
    main()
