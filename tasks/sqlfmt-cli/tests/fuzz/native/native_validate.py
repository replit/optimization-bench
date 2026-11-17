#!/usr/bin/env python3
"""Task-local development validation through the unmodified shared executors.

No qualification or scoring. Private outputs stay in the private working root.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

TASK = Path(__file__).resolve().parents[3]
REPO = TASK.parents[1]
sys.path.insert(0, str(REPO / "tools/runner"))
from runner import Context, LocalExecutor, fingerprint, run_case  # noqa: E402
from docker_executor import DockerExecutor  # noqa: E402

WORKLOAD = TASK / "environment/workload"
spec = importlib.util.spec_from_file_location("sqlfmt_task", WORKLOAD / "task.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def command(args, log):
    with log.open("wb") as stream:
        result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError("Development command failed. Inspect its public build log.")


def observe(build, inputs, image, work, docker=True, native_only=False):
    expected = {r["path"]: (r["expect_len"], r["expect_sha"]) for r in adapter.references(inputs)}
    reports, outputs = {}, {}
    for case in adapter.cases(inputs):
        if native_only and case.id != "native-batch":
            continue
        with tempfile.TemporaryDirectory(prefix="case-", dir=work) as directory:
            ctx = Context(Path(directory), build, inputs, Path(directory), WORKLOAD, docker=docker)
            executor = DockerExecutor(image, cpuset="0-7", task=TASK) if docker else LocalExecutor()
            observed, steps = run_case(adapter, case, ctx, executor)
            outputs.update(observed)
            reports[case.id] = {
                "matches": all(fingerprint(data) == expected[name] for name, data in observed.items())
                           and set(observed) == (
                               {"native-batch.stdout", "native-batch.stderr"} if case.id == "native-batch"
                               else set(expected) - {"native-batch.stdout", "native-batch.stderr"}),
                "time_ns": executor.case_time_ns if docker else None,
                "steps": len(steps),
                "live_leftovers": sum(len(s.leftovers) for s in steps),
            }
    return reports, outputs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--private-inputs", type=Path)
    args = p.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    args.work.chmod(0o700)
    source = args.work / "original"
    cid = subprocess.check_output(["docker", "create", args.image], text=True).strip()
    try:
        subprocess.run(["docker", "cp", cid + ":/app/sqlfmt", str(source)], check=True,
                       stdout=subprocess.DEVNULL)
        # Stage only the image's authenticated public input payloads.
        subprocess.run(["docker", "cp", cid + ":/app/workload/inputs", str(args.work / "public")],
                       check=True, stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(["docker", "rm", cid], check=True, stdout=subprocess.DEVNULL)
    original_format = (source / "format/format.go").read_text()
    assert original_format.count("func Format(r io.Reader) (string, error) {") == 1
    reports, baseline_outputs = {}, None
    for name, insertion in [
        ("baseline", ""), ("noop", ""),
        ("wrong", 'return "deliberately wrong SQL\\n", nil\n'),
        ("omitted", 'return "", nil\n'),
        ("slower", "time.Sleep(3 * time.Millisecond)\n"),
    ]:
        candidate = args.work / (name + "-source")
        shutil.copytree(source, candidate)
        text = original_format
        if insertion:
            text = text.replace("func Format(r io.Reader) (string, error) {",
                                "func Format(r io.Reader) (string, error) {\n" + insertion, 1)
        if name == "slower":
            text = text.replace('import (\n', 'import (\n\t"time"\n', 1)
        (candidate / "format/format.go").write_text(text)
        build = args.work / (name + "-build")
        build.mkdir()
        command(["docker", "run", "--rm", "--network", "none", "--cpuset-cpus", "0-7",
                 "-v", f"{candidate}:/candidate:ro", "-v", f"{build}:/build",
                 "-e", "OPTIMBENCH_SOURCE=/candidate", args.image,
                 "sh", "/app/workload/build.sh", "/build"], args.work / (name + "-build.log"))
        assert (build / "project/format/format.go").read_text() == text
        observed, raw = observe(build, args.work / "public", args.image, args.work)
        if name == "baseline":
            baseline_outputs = raw
            local, local_raw = observe(build, args.work / "public", args.image, args.work, docker=False)
            assert local_raw == raw and all(r["matches"] for r in local.values())
        elif name in ("noop", "slower"):
            assert raw == baseline_outputs
        else:
            assert not any(r["matches"] for r in observed.values()), "Bad build escaped one workload"
        if name in ("baseline", "noop", "slower"):
            assert all(r["matches"] and r["live_leftovers"] == 0 for r in observed.values())
        if name in ("baseline", "noop") and args.private_inputs:
            private, _ = observe(build, args.private_inputs, args.image, args.work, native_only=True)
            assert all(r["matches"] and r["live_leftovers"] == 0 for r in private.values())
            observed["private-native"] = private["native-batch"]
        reports[name] = observed
        if name in ("baseline", "slower"):
            command(["docker", "run", "--rm", "--network", "none", "--cpuset-cpus", "0-7",
                     "-v", f"{build}:/build", "-w", "/build/project",
                     "-e", "GOCACHE=/build/go-cache", args.image,
                     "go", "test", "-json", "-count=1", "./..."],
                    args.work / (name + "-upstream.jsonl"))
    for workload in ("batch", "native-batch"):
        assert reports["slower"][workload]["time_ns"] > reports["baseline"][workload]["time_ns"]
    ledgers = []
    for name in ("baseline", "slower"):
        outcomes = {}
        for line in (args.work / (name + "-upstream.jsonl")).read_text().splitlines():
            event = json.loads(line)
            if event.get("Test") and event["Action"] in ("pass", "skip", "fail"):
                outcomes[event["Package"] + "::" + event["Test"]] = event["Action"]
        assert outcomes and "fail" not in outcomes.values()
        ledgers.append(outcomes)
    assert ledgers[0] == ledgers[1]
    summary = {
        "image": args.image, "cpuset": "0-7", "memory": "16g",
        "whole_process_shared_clock": True, "candidate_loaded_by_both_workloads": True,
        "original_public_commitments_preserved": True,
        "public_executor_parity": True, "upstream_identity_outcomes_equal": True,
        "controls": reports, "qualification": False, "aa_noise_test": "not performed",
        "full_private_historical_suite": "not inspected or run",
    }
    (args.work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
