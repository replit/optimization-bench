#!/usr/bin/env python3
"""Bind pinned-task overlay capabilities to an actual SQLGlot coverage smoke."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework-root", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--overlay-record", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output.resolve()
    framework = args.framework_root.resolve()
    sys.path.insert(0, str(framework))
    sys.path.insert(0, str(framework / "tools/runner"))
    from runner import Context, Step
    from docker_executor import DockerExecutor
    from tools.fuzz_runners import coverage, receipt
    sys.path.insert(0, str(REPO))
    from judge import policy
    from judge.verifier import build_inventory

    overlay = json.loads(args.overlay_record.read_text())
    image = overlay["runner_image"]
    actual = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    assert actual == image
    image_env = json.loads(subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image], text=True))
    assert "PIP_NO_INDEX=1" in image_env
    cid = subprocess.check_output(["docker", "create", image], text=True).strip()
    try:
        subprocess.run(["docker", "cp", cid + ":/app/baseline-build", str(out / "pinned-build")],
                       check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "cp", cid + ":/app/sqlglot", str(out / "pinned-source")],
                       check=True, stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(["docker", "rm", cid], check=True, stdout=subprocess.DEVNULL)
    build_sha = build_inventory(out / "pinned-build")
    rules = json.loads((ROOT.parents[1] / "release.json").read_text())["source_policy"]
    source_sha = policy.source_digest(policy.envelope(
        out / "pinned-source", out / "pinned-source", rules))
    scratch, empty = out / "scratch", out / "empty"
    scratch.mkdir()
    empty.mkdir()
    context = Context(scratch=scratch, build=empty, inputs=empty, projects=empty,
                      workload=empty, docker=True)
    executor = DockerExecutor(image, cpuset="0-7", extra_docker_flags=[
        "-v", f"{ROOT}:/verifier-fuzz:ro"])
    python = "/opt/fuzz-python/bin/python"
    commands = [
        Step([python, "-c",
              "import os,json,sys,importlib.metadata as m;"
              "assert os.environ['PIP_NO_INDEX']=='1';"
              "print(json.dumps({'python':sys.version,'PIP_NO_INDEX':'1',"
              "'tools':{k:m.version(k) for k in ('atheris','hypothesis','coverage')}}))"]),
        Step([python, "/verifier-fuzz/property_generate.py", "--seed", "1729",
              "--examples", "30", "--output", "/scratch/property"]),
        Step([python, "-m", "coverage", "run", "--branch", "--parallel-mode",
              "/verifier-fuzz/driver.py", "--runtime", "/app/baseline-build/runtime",
              "--case", "/verifier-fuzz/sample-case.json", "--build-sha256", build_sha,
              "--output", "/scratch/observation.json"],
             env={"COVERAGE_FILE": "/scratch/.coverage"}),
        Step([python, "-m", "coverage", "combine", "/scratch"],
             env={"COVERAGE_FILE": "/scratch/.coverage"}),
        Step([python, "-m", "coverage", "json", "-o", "/scratch/coverage.json"],
             env={"COVERAGE_FILE": "/scratch/.coverage"}),
        Step([python, "-c",
              "from pathlib import Path;"
              "p=Path('/scratch/atheris-seeds');p.mkdir();"
              "(p/'public-sql').write_bytes(bytes([0,0,2,0])+b'SELECT 1 AS a')"]),
        Step([python, "/verifier-fuzz/coverage_guide.py", "/scratch/atheris-seeds",
              "-seed=1729", "-runs=30", "-max_len=256"],
             env={"SQLGLOT_FUZZ_RUNTIME": "/app/baseline-build/runtime",
                  "SQLGLOT_FUZZ_BUILD_SHA256": build_sha,
                  "SQLGLOT_FUZZ_CORPUS_OUT": "/scratch/atheris-discovery"},
             timeout=120),
    ]
    # Shared DockerExecutor intentionally replaces the child environment. Carry
    # this protected parent's offline value explicitly; do not confuse the
    # sanitized Step environment with the image configuration.
    for step in commands:
        step.env = {"PIP_NO_INDEX": "1", **step.env}
    results = executor.run(commands, context, {})
    step_rows = [{
        "argv": step.argv, "env": step.env, "exit_code": result.exit_code,
        "stdout_utf8": result.stdout.decode("utf-8", errors="replace"),
        "stderr_utf8": result.stderr.decode("utf-8", errors="replace"),
        "leftovers": result.leftovers, "telemetry": result.telemetry,
    } for step, result in zip(commands, results)]
    save(out / "steps.json", step_rows)
    assert len(results) == len(commands) and all(r.exit_code == 0 for r in results), step_rows
    tools = json.loads(results[0].stdout)
    assert tools["tools"] == {"atheris": "3.1.0", "hypothesis": "6.170.12", "coverage": "7.17.0"}
    raw = json.loads((scratch / "coverage.json").read_text())
    normalized = coverage.normalize("python", raw, "/app/baseline-build/runtime")
    save(out / "coverage-normalized.json", normalized)
    assert normalized["status"] == "available" and normalized["files"]
    capabilities = json.loads(args.capabilities.read_text())["capabilities"]
    build_record = receipt.build_receipt(
        language="python", runner_image=image, parent_task_image=overlay["parent_task_image"],
        source_sha256=source_sha, build_sha256=build_sha, features=["pure-python"],
        instrumentation="coverage.py-branch", flags=commands[2].argv[:6],
        tools=tools, capabilities=capabilities)
    save(out / "build-receipt.json", build_record)
    save(out / "coverage-receipt.json", receipt.coverage_receipt(
        build_receipt_sha256=receipt.sha256(out / "build-receipt.json"),
        raw_paths=[scratch / "coverage.json"], normalized_path=out / "coverage-normalized.json",
        base=out, expected_workers=["sqlglot-smoke-main"], observed_workers=["sqlglot-smoke-main"],
        source_sha256=source_sha))
    save(out / "summary.json", {
        "format": "optimbench-fuzz-v1", "scope": "Pinned-task capability and one-case coverage smoke",
        "source_sha256": source_sha, "build_sha256": build_sha, "runner_image": image,
        "parent_task_image": overlay["parent_task_image"], "tools": tools,
        "coverage_task_files": len(normalized["files"]),
        "coverage_status": normalized["status"], "runtime_offline_setting_preserved": True,
        "executor_environment_note": "Shared executor sanitizes image ENV; "
                                     "trusted steps explicitly preserve parent PIP_NO_INDEX=1.",
        "property_examples": json.loads((scratch / "property/order.json").read_text())["observed_examples"],
        "atheris_smoke_observations": len(
            (scratch / "atheris-discovery/order.jsonl").read_text().splitlines()),
        "atheris_scope": "30-run one-arm discovery smoke, not a 45-minute differential campaign",
        "changed_coverage_adequacy": "not-measured",
        "candidate_qualification": False, "timing_performed": False,
    })
    print((out / "summary.json").read_text())


if __name__ == "__main__":
    main()
