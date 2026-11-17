"""Verifier-owned xdiff facts and CLI/header requests; shared judge owns verdicts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .rc_driver import CASES, SURFACE, TASK

HERE = Path(__file__).resolve().parent
CLI_SURFACE = {"id": "git.diff.worktree", "visibility": "public",
               "contract_id": "git-diff-worktree-v1",
               "precondition_id": "git-public-indexed-text-v1"}


def CLI_PRECONDITION(case, surface):
    value = case.get("input", {})
    valid = (
        surface == CLI_SURFACE and case.get("task") == TASK
        and all(case.get(key) == surface[key] for key in ("contract_id", "precondition_id"))
        and case.get("surface_id") == surface["id"]
        and case.get("options") in ({"algorithm": "myers"}, {"algorithm": "histogram"})
        and value.get("fixture") == "indexed-public-original-lines"
        and type(value.get("bytes")) is int and 0 < value["bytes"] <= 262144
        and isinstance(value.get("sha256"), str) and len(value["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["sha256"])
        and case.get("faults") == []
    )
    return {"valid": bool(valid), "reason": "Indexed public text through documented git diff"}


def CLI_CASES(context):
    from judge.fuzzing_contract import case_digest

    folder = Path(context["out"]) / "git-cli-inputs"
    folder.mkdir(parents=True, exist_ok=False)
    source = (HERE.parent / "differential/original-lines.txt").read_bytes()[:1024]
    if not source or b"\0" in source:
        raise ValueError("Expected nonempty pinned public text")
    cases = []
    for size, repeats, algorithm in (("small", 1, "myers"), ("large", 128, "histogram")):
        tree = folder / size
        tree.mkdir()
        before = source * repeats
        after = b"".join(b"changed: " + line for line in before.splitlines(keepends=True))
        (tree / "text.txt").write_bytes(before)
        # Trusted, untimed fixture preparation. No candidate code or private data.
        env = {"PATH": os.defpath, "HOME": str(tree), "LC_ALL": "C",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        for argv in (
            ["git", "init", "--quiet", "--template=", "--object-format=sha1",
             "--initial-branch=main", str(tree)],
            ["git", "-C", str(tree), "-c", "core.autocrlf=false", "add", "--", "text.txt"],
        ):
            subprocess.run(argv, env=env, check=True, capture_output=True, timeout=30)
        (tree / "text.txt").write_bytes(after)
        case = {"task": TASK, "surface_id": CLI_SURFACE["id"],
                "contract_id": CLI_SURFACE["contract_id"],
                "precondition_id": CLI_SURFACE["precondition_id"],
                "options": {"algorithm": algorithm}, "faults": [],
                "input": {"fixture": "indexed-public-original-lines", "bytes": len(after),
                          "sha256": hashlib.sha256(after).hexdigest()}}
        case["case_sha256"] = case_digest(case)
        cases.append({
            "case": case, "surface": CLI_SURFACE, "payload_class": size,
            "step": {"argv": ["{build}/project/git", "-c", "safe.directory=*",
                              "-c", "core.fsmonitor=false",
                              "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                              "--diff-algorithm=" + algorithm, "--", "text.txt"],
                     # build.sh uses prefix=/root, so system config resolves to
                     # /root/etc/git{config,attributes}; the non-root worker gets
                     # EACCES (fatal for config, a stderr warning for attributes).
                     "cwd": ".", "env": {"GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1"},
                     "timeout": 60},
            "inputs": str(tree), "watch_inputs": ["text.txt"],
        })
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases,
            "readonly_applicability_evidence": {
                "scope": "The admitted git diff invocation reads the worktree and index and writes stdout/stderr only.",
                "source": "Cases pass no --output or other file-writing option; git diff then emits only to stdout.",
                "limits": "No output-file mode is admitted. No exemption for native library gates."}}


def HEADER_CONSUMERS(context):
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(HERE / "header-manifest.json")}


def PRECONDITION(case, surface):
    options = case.get("options", {})
    valid = (
        case.get("task") == TASK and case.get("surface_id") == SURFACE["id"]
        and case.get("contract_id") == SURFACE["contract_id"]
        and case.get("precondition_id") == SURFACE["precondition_id"]
        and all(type(options.get(k)) is int for k in ("scenario", "flags", "fail_callback"))
        and tuple(options.get(k) for k in ("scenario", "flags", "fail_callback")) in CASES
        and case.get("input") == {"fixture": "rc_probe.c:fixed-documented-mmfiles-v1"}
    )
    return {"valid": bool(valid), "reason": "Initialized bounded xdiff task-contract cases"}


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "git-rc-library"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["image"], context.get("cpuset"),
           context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_git_rc_facts", {})
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="git-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]),
                          task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"], extra_docker_flags=[
                *HARDENING,
                "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
            ], tmpfs="/tmp:rw,nosuid,nodev,size=1g")
            command = ["python3", "/verifier-fuzz/rc_driver.py",
                       "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                       "--baseline-sha", context["builds"]["baseline"],
                       "--candidate-sha", context["builds"]["candidate"],
                       "--out", "/scratch/facts"]
            results = executor.run([Step(command, timeout=600)], ctx, {})
            for i, result in enumerate(results):
                (output / f"step-{i}.stdout").write_bytes(result.stdout)
                (output / f"step-{i}.stderr").write_bytes(result.stderr)
            if (scratch / "facts").is_dir():
                shutil.copytree(scratch / "facts", output / "facts", dirs_exist_ok=True)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"],
                "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results]}, indent=2) + "\n")
            if any(r.exit_code != 0 for r in results):
                raise ValueError("Git RC observer failed; inspect retained compile/transport evidence")
        cache["key"] = key
    return json.loads((output / "facts" / filename).read_text())


def stream_faults(context):
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process,
         "aliasing": aliasing}
