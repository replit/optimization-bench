#!/usr/bin/env python3
"""Rebuild selected task images and record exact public parity via both executors."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import references
sys.path.insert(0, str(ROOT))
from judge import public_input_preflight, public_packet


def run(command, log, **kwargs):
    with log.open("wb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command exited {result.returncode}: {' '.join(map(str, command))}; {log}")


def load(task):
    spec = importlib.util.spec_from_file_location("replayed_task", task / "environment/workload/task.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse(path):
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) == 3:
            name, size, digest = fields
            if name in result:
                raise ValueError("Duplicate public output: " + name)
            result[name] = (int(size), digest)
    return result


def replay(name, cpuset, output, skip_build):
    task = ROOT / "tasks" / name
    target = output / name
    target.mkdir(parents=True, exist_ok=True)
    image = f"consolidation:{name}"
    receipt = {"task": name, "cpuset": cpuset, "claimable": False, "passed": False}
    try:
        if not skip_build:
            public_input_preflight.ensure_public_inputs(task)
            run(["docker", "build", "--cpuset-cpus", cpuset, "-t", image,
                 str(task / "environment")], target / "image-build.log",
                env={**os.environ, "DOCKER_BUILDKIT": "1"})
        receipt["image"] = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
        receipt["runner_sha256"] = hashlib.sha256((ROOT / "tools/runner/runner.py").read_bytes()).hexdigest()
        build = target / "build"
        projects = target / "projects"
        if build.exists():
            shutil.rmtree(build)
        if projects.exists():
            shutil.rmtree(projects)
        workload = target / "public-workload"
        if workload.exists():
            shutil.rmtree(workload)
        container = subprocess.check_output(["docker", "create", image], text=True).strip()
        try:
            run(["docker", "cp", f"{container}:/app/baseline-build", str(build)], target / "copy-build.log")
            run(["docker", "cp", f"{container}:/app/workload", str(workload)], target / "copy-public.log")
            # Project tasks require their original pre-staged inventories.
            copied = subprocess.run(["docker", "cp", f"{container}:/app/projects", str(projects)],
                                    capture_output=True)
            if copied.returncode:
                projects.mkdir()
        finally:
            subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)
        receipt["public_input_preflight"] = public_input_preflight.ensure_public_inputs(
            task, source=workload, fetch=True)
        run(["docker", "run", "--rm", "--network", "none", "--cpuset-cpus", cpuset, "--memory", "16g",
             "--mount", public_packet.mount((workload / "inputs").resolve(), "/app/workload/inputs"),
             image, "/app/baseline-build/run"], target / "local.tsv")
        run([sys.executable, str(ROOT / "tools/runner/runner.py"), "--task",
             str(task / "environment/workload/task.py"), "--build", str(build),
             "--inputs", str(workload / "inputs"),
             "--projects", str(projects), "--executor", "docker", "--image", image,
             "--cpuset", cpuset], target / "docker.tsv")
        module = load(task)
        expected = {doc["path"]: (doc["expect_len"], doc["expect_sha"])
                    for doc in references(module, workload / "inputs")}
        local, docker = parse(target / "local.tsv"), parse(target / "docker.tsv")
        receipt.update(public_cases=len(expected), local_exact=local == expected,
                       docker_exact=docker == expected, executor_parity=local == docker,
                       passed=local == docker == expected)
        if not receipt["passed"]:
            receipt["mismatches"] = sorted(name for name in expected.keys() | local.keys() | docker.keys()
                                           if expected.get(name) != local.get(name) or expected.get(name) != docker.get(name))
        # No-op public judge transport; negative build controls are a separate,
        # explicit recipe, never silently substituted with a fake adapter.
        run([sys.executable, "-m", "judge.verifier", "--task", str(task),
             "--baseline", str(build), "--candidate", str(build), "--projects", str(projects),
             "--image", image, "--cpuset", cpuset, "--cpu-class", "consolidation",
             "--public-inputs", str(workload / "inputs"),
             "--plumbing", "--dry-run", "--output", str(target / "noop.json")],
            target / "noop.log", cwd=ROOT)
        receipt["noop_accepted"] = json.loads((target / "noop.json").read_text())["correct"]
    except Exception as error:
        receipt["error"] = str(error)
    (target / "summary.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt), flush=True)
    return receipt


def controls(name, cpuset, output, with_upstream=False):
    """Real built-artifact negative controls, plus the original upstream suite."""
    from judge import gates, upstream
    task, target = ROOT / "tasks" / name, output / name
    target.mkdir(parents=True, exist_ok=True)
    build, candidate = target / "build", target / "negative-build"
    image = f"consolidation:{name}"
    report = {"task": name, "claimable": False}
    try:
        if not build.is_dir():
            raise ValueError("Run parity replay first to stage a real image build")
        if candidate.exists():
            shutil.rmtree(candidate)
        subprocess.run(["cp", "-a", "--reflink=auto", str(build), str(candidate)], check=True)
        binary = {"goldmark-gfm-html": "goldmark-gfm-html",
                  "jq-strip-openapi-descriptions": "driver",
                  "lame-mono-vbr-encode": "driver-runner",
                  "universal-ctags-c-symbol-tags": "ctags-bench",
                  "xz-lzma2-compress": "xz-driver", "ruff-project-lint": "ruff",
                  "lumen-js": "prog", "spacewasm-interp": "prog", "sqlfmt-cli": "sqlfmt",
                  "anydoc-markdown": "prog", "zrip-decode": "prog",
                  "charls-jpegls-encode": "charls-driver"}
        if name in binary:
            changed = candidate / binary[name]
            changed.write_text("#!/bin/sh\nprintf 'deliberately changed public output\\n'\n")
            changed.chmod(0o755)
        elif name == "terser-minify":
            changed = candidate / "project/lib/minify.js"
            changed.write_text("throw new Error('deliberately changed public output');\n" + changed.read_text())
        else:
            package = {"black-project-format": "black", "sqlfluff-project-lint": "sqlfluff",
                       "sqlglot-mozilla-transpile": "sqlglot"}[name]
            files = list((candidate / "runtime" / package).glob("__init__*"))
            if not files:
                raise ValueError("No actual candidate package initializer to mutate")
            for changed in files:
                if changed.suffix == ".py":
                    changed.write_text("raise RuntimeError('deliberately changed public output')\n" + changed.read_text())
                elif changed.suffix == ".so":
                    changed.write_bytes(b"deliberately invalid native candidate library\n")
        process = subprocess.run([sys.executable, "-m", "judge.verifier", "--task", str(task),
            "--baseline", str(build), "--candidate", str(candidate),
            "--projects", str(target / "projects"), "--image", image, "--cpuset", cpuset,
            "--public-inputs", str(target / "public-workload/inputs"),
            "--cpu-class", "consolidation", "--plumbing", "--dry-run",
            "--output", str(target / "negative.json")], cwd=ROOT, capture_output=True)
        (target / "negative.log").write_bytes(process.stdout + process.stderr)
        result = json.loads((target / "negative.json").read_text())
        report["output_changing_candidate_rejected"] = process.returncode == 1 and result.get("correct") is False
        report["negative_status"] = result["status"]
        if not report["output_changing_candidate_rejected"]:
            raise ValueError("Public negative was not an incorrect verdict: " + str(result))
        if with_upstream:
            source = target / "source"
            if not source.exists():
                container = subprocess.check_output(["docker", "create", image], text=True).strip()
                try:
                    subprocess.run(["docker", "cp", container + ":" + gates.source_path(task),
                                    str(source)], check=True, stdout=subprocess.DEVNULL)
                finally:
                    subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)
            immutable = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                                text=True).strip()
            ledger = gates.execute_suites(task, source, build, immutable, cpuset,
                                         target / "upstream-logs", output / "compiler-cache")
            # A source-identical/build-identical no-op reuses this evidence. No
            # second execution or comparison to unavailable historical ledgers is claimed.
            upstream.compare(ledger, ledger, name)
            from judge import policy
            from judge.source_policy import digest
            rules = json.loads((task / "release.json").read_text())["source_policy"]
            source_sha = digest(policy.envelope(source, source, rules)["files"])
            bound = {"task": name, "image": immutable, "source_sha256": source_sha,
                     "build_sha256": result["baseline_build_sha256"],
                     "baseline": ledger, "candidate": ledger, "claimable": False,
                     "candidate_evidence": "reused: identical pinned source and build"}
            (target / "upstream.json").write_text(json.dumps(bound, indent=2) + "\n")
            upstream.validate_receipt(target / "upstream.json", name, immutable,
                                      source_sha, result["baseline_build_sha256"])
            report["upstream_passed"] = True
            report["noop_upstream_evidence"] = "reused: identical pinned source and build"
        report["passed"] = True
    except Exception as error:
        report.update(passed=False, error=str(error))
    (target / "controls.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append")
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/ready.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument("--with-upstream", action="store_true")
    args = parser.parse_args()
    tasks = args.task or json.loads(args.dataset.read_text())["tasks"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # One or two disjoint eight-core lanes, within the host's actual affinity.
    available = sorted(os.sched_getaffinity(0))
    workers = min(2, len(available) // 8)
    if not workers:
        parser.exit(2, "At least eight available logical CPUs are required\n")
    cpusets = [",".join(map(str, available[i * 8:(i + 1) * 8])) for i in range(workers)]
    lanes = [tasks[i::workers] for i in range(workers)]
    def lane(items, cpuset):
        return [controls(task, cpuset, output, args.with_upstream) if args.controls_only else
                replay(task, cpuset, output, args.skip_build) for task in items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(lane, tasks, cpuset) for tasks, cpuset in zip(lanes, cpusets)]
        rows = [row for future in futures for row in future.result()]
    (output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0 if all(row["passed"] and (args.controls_only or row.get("noop_accepted")) for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
