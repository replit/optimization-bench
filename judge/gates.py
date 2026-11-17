"""Execute upstream gates using the same shared DockerExecutor as workloads.

This is verifier-host code. No backend-specific container supervisor exists.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import tomllib

from . import policy, ruff_source_policy, upstream
from .source_policy import digest
from .verifier import ROOT, HARDENING, build_inventory, image_id
from .telemetry import pinned
from .gate_contracts import require_baseline
from runner import Context, Step, HarnessError
from docker_executor import DockerExecutor

CONTROLS = Path(__file__).with_name("controls")
SOURCES = {
    "terser-minify": "/app/terser", "black-project-format": "/app/black",
    "sqlfluff-project-lint": "/app/sqlfluff", "sqlglot-mozilla-transpile": "/app/sqlglot",
    "babel-project-compile": "/app/babel", "ruff-project-lint": "/app/ruff",
}


def source_path(task):
    """Resolve the pinned source from the common shell, not a batch allowlist."""
    settings = json.loads((task / "tests/judge.json").read_text())
    if settings.get("source_path"):
        return settings["source_path"]
    if task.name in SOURCES:
        return SOURCES[task.name]
    config = tomllib.loads((task / "task.toml").read_text())
    roots = json.loads((task / "release.json").read_text())["source_policy"]["source_roots"]
    candidates = set()
    for artifact in config["artifacts"]:
        matches = [root for root in roots if artifact["source"].endswith("/" + root)]
        if matches:
            root = max(matches, key=len)
            candidates.add(artifact["source"][:-len(root)].rstrip("/"))
    if len(candidates) != 1:
        raise ValueError("Cannot resolve a unique pristine source directory")
    return candidates.pop()


class UpstreamRejected(ValueError):
    def __init__(self, reason, receipts):
        super().__init__(reason)
        self.receipts = receipts


def cargo_outcomes(log, source, allow_failures=False):
    """Original Ruff identity gate, with protected doc-comment identity normalization."""
    target, tests, summaries = None, [], []
    for line in log.splitlines():
        if re.match(r"\s*(?:Running |Doc-tests )", line):
            target = re.sub(r"\([^)]*/([^/]+)-[0-9a-f]+\)$", r"(\1)", line.strip())
        match = re.match(r"^test (.+) \.\.\. (ok|ignored|FAILED)(?:[, ].*)?$", line)
        if match:
            if target is None:
                raise ValueError("Test without target")
            name = match[1]
            doc = re.match(r"^(crates/.+?\.rs) - (.*) \(line (\d+)\)(.*)$", name)
            if doc:
                text = (source / doc[1]).read_text()
                offset = sum(len(x) for x in text.splitlines(keepends=True)[:int(doc[3]) - 1])
                index = sum(x.start() <= offset for x in ruff_source_policy.DOCS.finditer(text))
                name = f"{doc[1]} - {doc[2]} (documentation comment {index}){doc[4]}"
            tests.append({"target": target, "name": name, "status": match[2]})
        match = re.match(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored;", line)
        if match:
            summaries.append({"target": target, "outcome": match[1], "passed": int(match[2]),
                              "failed": int(match[3]), "ignored": int(match[4])})
    if (not summaries or sum(x["passed"] + x["ignored"] + x["failed"] for x in summaries) != len(tests)
            or len({(x["target"], x["name"]) for x in tests}) != len(tests)
            or (not allow_failures and any(x["failed"] or x["outcome"] != "ok" for x in summaries))):
        raise ValueError("Ruff upstream identity/outcome mismatch")
    return {"tests": sorted(tests, key=lambda x: (x["target"], x["name"])), "summaries": summaries}


def execute_suites(task, source, build, image, cpuset, output_dir=None, cache_dir=None, fail_fast=False):
    settings = json.loads((task / "tests/judge.json").read_text())
    if "upstream_gate" in settings:
        from .configured_gates import execute
        return execute(task, source, build, image, cpuset, output_dir)
    release = json.loads((task / "release.json").read_text())
    timeout = release.get("upstream_timeout", 7200)
    receipts = {}

    def run(name, command, environment=None, setup=None, report=None):
        with tempfile.TemporaryDirectory(prefix="judge-upstream-") as temporary:
            scratch = Path(temporary)
            shutil.copytree(source, scratch / "source", symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
            env = {"PATH": "/toolchain/bin:/usr/local/bin:/usr/bin:/bin", "CI": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                   "PYTHONPATH": "/app/build/runtime:/judge-controls",
                   "PYTHONSAFEPATH": "1", "BLACK_NUM_WORKERS": "8", "RAYON_NUM_THREADS": "8",
                   "CARGO_BUILD_JOBS": "8", "INSTA_UPDATE": "no",
                   "CARGO_PROFILE_DEV_DEBUG": "line-tables-only",
                   "CARGO_HOME": "/scratch/cargo-home", "CARGO_TARGET_DIR": "/scratch/target",
                   "PIP_NO_INDEX": "1", **release.get("upstream_test_environment", {}),
                   **(environment or {})}
            if task.name == "terser-minify":
                (scratch / "source/node_modules").symlink_to("/app/deps/node_modules")
                # CommonJS/CLI tests load package.json's main entry, not only
                # the ESM source. Test the bundle produced by this exact build.
                shutil.rmtree(scratch / "source/dist", ignore_errors=True)
                shutil.copytree(build / "project/dist", scratch / "source/dist", symlinks=True)
                compression = scratch / "source/test/compress.js"
                text = compression.read_text()
                anchor = "            if (!await test_case(tests[i])) {"
                if text.count(anchor) != 1:
                    raise HarnessError("Unsupported Terser compression identity hook")
                compression.write_text(text.replace(anchor,
                    '            const judgePassed = await test_case(tests[i]);\n'
                    '            console.log("JUDGE_TEST " + JSON.stringify({target: "compression", '
                    'name: [tests[i].file.replace(process.cwd() + "/", "").replace(/:\\d+$/, ""), i], '
                    'status: judgePassed ? "passed" : "failed"}));\n'
                    '            if (!judgePassed) {'))
            if task.name == "black-project-format" and name == "pure_incompatible_with_mypyc":
                shutil.copy2(build / "runtime/_black_version.py", scratch / "source/src/_black_version.py")
            ctx = Context(scratch, build, task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            flags = [
                *HARDENING, "-v", f"{CONTROLS}:/judge-controls:ro",
                *([] if task.name != "babel-project-compile" else [
                    "-v", f"{source}:/app/babel:ro", "-v", f"{scratch}:/work"])]
            if cache_dir is not None and task.name == "ruff-project-lint":
                admitted = policy.envelope(source, source, release["source_policy"])
                key = hashlib.sha256((image + policy.source_digest(admitted) + "dev-line-tables-v1").encode()).hexdigest()
                target = Path(cache_dir).resolve() / key
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o777)
                flags.extend(["-v", f"{target}:/scratch/target"])
            if fail_fast:
                env["JUDGE_FAIL_FAST"] = "1"
            executor = DockerExecutor(image, cpuset, extra_docker_flags=flags,
                tmpfs="/tmp:rw,nosuid,nodev,size=2g")
            # The runner deliberately fixes PATH in base_env. Restore the
            # trusted toolchain prefix in argv, not via an env override it masks.
            prefix = ["/usr/bin/env", "PATH=/app/build/runtime/bin:/toolchain/bin:/usr/local/bin:/usr/bin:/bin"]
            steps = [Step([*prefix, *argv], "source", timeout=timeout) for argv in (setup or [])]
            steps.append(Step([*prefix, *command], "source", timeout=timeout))
            result = executor.run(steps, ctx, env)
            record = {"passed": False,
                      "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                                 "telemetry": r.telemetry,
                                 "stdout_sha256": hashlib.sha256(r.stdout).hexdigest(),
                                 "stderr_sha256": hashlib.sha256(r.stderr).hexdigest()} for r in result]}
            receipts[name] = record
            if output_dir is not None:
                evidence = Path(output_dir) / name
                evidence.mkdir(parents=True, exist_ok=True)
                for index, item in enumerate(result):
                    (evidence / f"step-{index}.stdout").write_bytes(item.stdout)
                    (evidence / f"step-{index}.stderr").write_bytes(item.stderr)
                if report and (scratch / report).is_file():
                    shutil.copyfile(scratch / report, evidence / "report.json")
                    record["observed_report"] = json.loads((scratch / report).read_text())
                (evidence / "steps.json").write_text(json.dumps(record["steps"], indent=2) + "\n")
            if any(r.exit_code != 0 for r in result):
                if task.name == "ruff-project-lint":
                    try:
                        record["observed_outcomes"] = cargo_outcomes(
                            result[-1].stdout.decode(errors="replace"), source, allow_failures=True)
                    except ValueError as error:
                        record["ledger_error"] = str(error)
                raise UpstreamRejected("Upstream command failed: " + name, receipts)
            output = result[-1].stdout.decode(errors="replace")
            errors = result[-1].stderr.decode(errors="replace")
            if report:
                value = json.loads((scratch / report).read_text())
                if value.get("passed") is False:
                    raise UpstreamRejected("Upstream identity gate rejected output: " + name, receipts)
                outcomes = upstream.pytest_outcomes(value) if "exit_status" in value else value
            elif task.name == "terser-minify":
                outcomes = upstream.terser_outcomes(output)
            elif task.name == "ruff-project-lint":
                outcomes = cargo_outcomes(errors + "\n" + output, source)
            else:
                # Babel's archived checkers return their exact identity-check result.
                value = json.loads(output)
                if not value.get("identities_match") or value.get("failed") or not value.get("total"):
                    raise ValueError("Babel archived identity checker failed")
                outcomes = value
            receipts[name] = {"passed": True, "outcomes": outcomes,
                              "steps": record["steps"]}

    task_id = task.name
    if task_id in ("black-project-format", "sqlfluff-project-lint"):
        package = "black" if task_id.startswith("black") else "sqlfluff"
        env = {"PYTEST_PLUGINS": "gate_pytest", "OPTIMBENCH_PACKAGE": package,
               "OPTIMBENCH_REPORT": "/scratch/upstream.json"}
        setup = []
        if package == "sqlfluff":
            setup = [["python", "-m", "pip", "install", "--no-deps", "--no-build-isolation",
                      "--target", "/scratch/plugin", "/scratch/source/plugins/sqlfluff-plugin-example"]]
            env["PYTHONPATH"] = "/scratch/plugin:/app/build/runtime:/judge-controls"
            env["SQLFLUFF_TESTENV"] = "1"
        command = list(release["upstream_test_command"])
        if "-n" in command:
            command[command.index("-n") + 1] = "8"
        if fail_fast:
            command.append("-x")
        run("upstream", command, env, setup, "upstream.json")
        if package == "black":
            run("pure_incompatible_with_mypyc",
                ["python", "-m", "pytest", "tests", "-k", "incompatible_with_mypyc", "-n", "8"],
                {**env, "PYTHONPATH": "/scratch/source/src:/app/build/runtime:/judge-controls",
                 "OPTIMBENCH_IMPORT_ROOT": "/scratch/source/src"}, report="upstream.json")
        else:
            receipts["example_plugin"] = receipts["upstream"]
    elif task_id == "terser-minify":
        # Run candidate production build with pristine upstream tests.
        run("upstream", ["/bin/sh", "-c",
            "node test/compress.js && node_modules/.bin/mocha test/mocha "
            "--reporter /judge-controls/terser_reporter.cjs"])
    elif task_id == "sqlglot-mozilla-transpile":
        run("upstream", ["python", "/judge-controls/unittest_gate.py", "/scratch/upstream.json"],
            report="upstream.json")
    elif task_id == "ruff-project-lint":
        # Cargo emits suite headers to stderr and test identities to stdout.
        # Merge *at execution time* so target ownership is preserved; concatenating
        # two captured streams afterwards assigns tests to the wrong suite.
        run("upstream", ["/bin/sh", "-c", 'exec "$@" 2>&1', "judge-cargo",
                         *release["upstream_test_command"]])
        receipts["all_features"] = receipts["upstream"]
    elif task_id == "babel-project-compile":
        for name, group in [
            ("upstream", "main"), ("polyfills", "polyfills"), ("preset_modules", "preset-modules"),
            ("archived_historical120", "historical120"), ("archived_target117", "target117"),
        ]:
            run(name, ["python3", "/judge-controls/babel_gate.py", group],
                report="upstream.json")
    else:
        raise ValueError("Unknown task: no configured upstream gate")
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("task", "pristine", "source", "baseline", "candidate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--baseline-receipt", type=Path,
                        help="Reuse a trusted successful baseline bound to this pristine source/image/build")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path, help="Optional verifier-only source/image-keyed Rust compiler cache")
    parser.add_argument("--fail-fast", action="store_true", help="Stop a negative control after its first upstream failure")
    args = parser.parse_args()
    pinned(args.cpuset)
    task, pristine, source, baseline, candidate = (
        p.resolve() for p in (args.task, args.pristine, args.source, args.baseline, args.candidate))
    source_policy = json.loads((task / "release.json").read_text())["source_policy"]
    admitted = policy.envelope(source, pristine, source_policy)
    image = image_id(args.image)
    baseline_source = policy.source_digest(policy.envelope(pristine, pristine, source_policy))
    baseline_build = build_inventory(baseline)
    evidence = args.evidence_dir or args.output.parent / (args.output.stem + "-evidence")
    result = {"task": task.name, "image": image, "claimable": False,
              "scope": "upstream admission plumbing; no performance or held-out claim",
              "baseline_source_sha256": baseline_source, "baseline_build_sha256": baseline_build,
        "source_sha256": policy.source_digest(admitted), "build_sha256": build_inventory(candidate),
        "status": "invalid run"}
    try:
        if args.baseline_receipt:
            cached = json.loads(args.baseline_receipt.read_text())
            if (cached["task"] != task.name or cached["image"] != image or
                    cached["baseline_source_sha256"] != baseline_source or
                    cached["baseline_build_sha256"] != baseline_build):
                raise HarnessError("Baseline receipt identity mismatch")
            a = cached["baseline"]
            upstream.compare(a, a, task.name)
        else:
            a = execute_suites(task, pristine, baseline, image, args.cpuset, evidence / "baseline", args.cache_dir)
        result["baseline"] = a
        result["baseline_counts"] = require_baseline(task.name, task, a)
        try:
            b = execute_suites(task, source, candidate, image, args.cpuset, evidence / "candidate",
                               args.cache_dir, args.fail_fast)
            result["candidate"] = b
            upstream.compare(a, b, task.name)
            result.update(status="passed", correct=True)
        except UpstreamRejected as error:
            result.update(status="rejected", correct=False, reason=str(error), candidate=error.receipts)
        except ValueError as error:
            result.update(status="rejected", correct=False, reason=str(error))
    except (ValueError, HarnessError, OSError) as error:
        result.update(status="invalid run", reason=str(error))
        if isinstance(error, UpstreamRejected):
            result["baseline_failure"] = error.receipts
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["status"] == "passed" else 1 if result["status"] == "rejected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
