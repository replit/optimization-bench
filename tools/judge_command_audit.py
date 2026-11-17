"""Inventory judge process-launch sites and their host/worker dependency boundary.

Static binary names plus dynamic argv producers are both retained. This does not
pretend to enumerate arbitrary child commands of compilers or candidate programs:
worker recipes/package locks own their closure, while explicit host generators
must declare their host tools separately. Review/refresh the frozen-head manifest.
Task helpers may declare per-function EXTERNAL_COMMAND_CONTEXTS; undeclared
helper contexts remain unresolved, not inferred host dependencies or exemptions.
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/judge_external_commands.json"
HOST = {
    "sh": "POSIX shell (dash on Debian/Ubuntu) for setup; worker build/test scripts use the image shell",
    "python3": "Dedicated judge/requirements-interface.lock venv (same sys.executable for host Python helpers)",
    "git": "git; pinned public git-history acquisition/verification and scoped LFS metadata",
    "git-lfs": "git-lfs; initialize this checkout with git lfs install --local --skip-smudge",
    "docker": "Docker daemon/CLI with cgroup v2 and buildx plugin",
    "nm": "GNU binutils; used on host by judge.interfaces.symbols",
    "c++filt": "GNU binutils; host judge.native_surface uses -n for proven Itanium weak/vtable symbol mapping, preserving unknown names",
    "lscpu": "util-linux; registered-host CPU topology fingerprint, missing facts cannot qualify a host",
    "uname": "coreutils; registered-host kernel/architecture/local-daemon identity fingerprint",
    "containerd": "Docker runtime's matching containerd CLI; registered-host runtime fingerprint, not a candidate dependency",
    "ip": "iproute2; local Docker bridge MTU inspection/fix",
    "iptables": "iptables; IPv4 Docker-only MSS clamp inspection/fix",
    "ip6tables": "iptables package; conditional on IPv6-enabled Docker bridges",
    "ffmpeg": "ffmpeg; conditional public png-rgb24 acquisition/transport conversion",
    "pip": "Only $JUDGE_VENV/bin/pip during setup, provided by python3-venv; not a global pip requirement",
}
CALLS = {"run", "Popen", "check_output", "check_call", "call", "execv", "execvp", "system", "which"}
TASK_CONTEXTS = {"host", "host-generator", "pinned-worker", "pinned-worker-verifier-observer"}


def sources(root):
    root = Path(root)
    manifest = root / "tools/judge_external_commands.json"
    bound = set(json.loads(manifest.read_text())["source_sha256"]) if manifest.is_file() else set()
    result = set()
    for base in ("judge", "tools/runner", "tools/fuzz_runners"):
        result.update((root / base).glob("*.py"))
    for name in ("tools/payload_check.py", "tools/stage_payload.py",
                 "tools/scoring_host.py", "tools/qualify_aa.py",
                 "tools/judge_command_audit.py",
                 "tools/public_runtime_packet.py", "tools/export_source_snapshot.py",
                 "tools/export_candidate_source.py",
                 "tools/prepare_verifier_assets.py", "tools/recover_serde_json_goldens.py",
                 "tools/runner/python_startup/sitecustomize.py",
                 "tools/update_catalog.py", "tools/check_tasks.py", "tools/heldout_policy.py"):
        if (root / name).is_file():
            result.add(root / name)
    for task in (root / "tasks").iterdir():
        if not task.is_dir():
            continue
        payload = task / "payload.json"
        # Input programs are corpus data, not launchers; inventory binds this declaration.
        inputs = set()
        for row in json.loads(payload.read_text())["files"] if payload.is_file() else ():
            value = row["destination"]
            relative = PurePosixPath(value)
            if (not relative.parts or relative.is_absolute() or ".." in relative.parts
                    or relative.as_posix() != value or "\\" in value):
                raise ValueError("Input destination must be normalized and task-relative: " + task.name)
            inputs.add(value)
        candidates = list(task.glob("*.py"))
        for base in ("environment", "tests"):
            candidates.extend((task / base).rglob("*.py"))
        for path in candidates:
            if (path.is_file() and not path.is_symlink() and not path.name.startswith("test_")
                    and (path.relative_to(root).as_posix() in bound
                         or path.relative_to(task).as_posix() not in inputs)
                    and not set(path.relative_to(task).parts).intersection({
                        "fixture", "fixtures", "testdata", "data", "inputs", "files", "expected", "receipts",
                        "__pycache__", ".venv"})):
                result.add(path)
    return sorted(result)


def head(expression, assignments, seen=()):
    if isinstance(expression, (ast.List, ast.Tuple)) and expression.elts:
        return head(expression.elts[0], assignments, seen)
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        return head(expression.left, assignments, seen)
    if isinstance(expression, ast.Name) and expression.id in assignments and expression.id not in seen:
        return head(assignments[expression.id], assignments, (*seen, expression.id))
    if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name) and expression.func.id == "_git_args":
        return "git"
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name) and expression.value.id == "sys" and expression.attr == "executable":
        return "python3"
    return None


def context(path, function, executable, kind, task_contexts=None):
    if kind == "Step-argv-producer":
        return "pinned-worker-step-or-explicit-local-executor"
    if path in ("tools/fuzz_runners/probe.py", "tools/fuzz_runners/header_matrix.py") or (
            path == "tools/fuzz_runners/cli_faults.py" and function == "worker"):
        return "pinned-worker-verifier-observer"
    if path.startswith("tasks/"):
        if task_contexts and function in task_contexts:
            return task_contexts[function]
        if path.endswith("tests/differential/generate.py"):
            return "host-generator"
        if "/environment/workload/" in path:
            return "pinned-worker"
        return "task-helper-context-requires-review"
    if path == "judge/c_preprocessor.py":
        return "pinned-worker-or-explicit-direct-host-compiler"
    if path in ("tools/runner/shim.py",):
        return "pinned-worker-dynamic-task-command"
    if path == "tools/runner/runner.py":
        return "explicit-local-executor-or-pinned-worker"
    if path == "tools/scoring_host.py":
        return "host-setup"
    if path in ("tools/public_runtime_packet.py", "tools/export_source_snapshot.py",
                "tools/export_candidate_source.py", "tools/prepare_verifier_assets.py"):
        return "host"
    if executable in HOST:
        return "host"
    if executable is None:
        return "host-or-worker-dynamic-reviewed-producer"
    return "host"


def inspect_source(path, root):
    relative = path.relative_to(root).as_posix()
    source = path.read_text()
    tree = ast.parse(source)
    records = []

    def assignments_in(node):
        values, pending = {}, [node]
        while pending:
            child = pending.pop()
            if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = child.value
            pending.extend(reversed(list(ast.iter_child_nodes(child))))
        return values

    def visit(node, function="<module>", assignments=None):
        assignments = assignments or {}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
            assignments = {**assignments, **assignments_in(node)}
        # Assignment lookup is an inventory aid, not a semantic execution model.
        # Dynamic/conditional producers remain visible for manual review.
        if isinstance(node, ast.Call):
            call = node.func
            kind = None
            if isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name):
                if call.value.id in ("subprocess", "shutil", "os") and call.attr in CALLS:
                    kind = call.value.id + "." + call.attr
            elif isinstance(call, ast.Name) and call.id == "Step":
                kind = "Step-argv-producer"
            elif relative == "tools/scoring_host.py" and isinstance(call, ast.Name) and call.id == "run":
                kind = "host-command-template"
            if kind and node.args:
                expression = node.args[0]
                # Enclosing assignments are added by the second pass below.
                records.append((node, function, kind, expression, head(expression, assignments)))
        for child in ast.iter_child_nodes(node):
            visit(child, function, assignments)
    module_assignments = assignments_in(tree)
    task_contexts = {}
    if relative.startswith("tasks/") and "EXTERNAL_COMMAND_CONTEXTS" in module_assignments:
        task_contexts = ast.literal_eval(module_assignments["EXTERNAL_COMMAND_CONTEXTS"])
        if (not isinstance(task_contexts, dict) or
                any(not isinstance(key, str) or not isinstance(value, str) or value not in TASK_CONTEXTS
                    for key, value in task_contexts.items())):
            raise ValueError("Invalid task external-command context declaration")
    visit(tree, assignments=module_assignments)
    rows = []
    for node, function, kind, expression, executable in records:
        segment = ast.get_source_segment(source, expression)
        if segment is None:
            raise ValueError("Cannot bind a process argument to its source segment")
        # Do not emit whole shell scripts, URIs, configs or embedded credentials.
        if executable and (len(executable) > 180 or "\n" in executable or "://" in executable):
            executable = None
        rows.append({
            "source": relative, "function": function, "line": node.lineno, "call": kind,
            "binary": executable, "argument_form": type(expression).__name__,
            # ast.dump's optional/empty fields change between Python minors.
            # Source bytes (universal LF, otherwise exact) do not. Whitespace
            # changes are deliberately reviewable and source hashes stay strict.
            "argv_expression_sha256": hashlib.sha256(segment.replace("\r\n", "\n").encode("utf-8")).hexdigest(),
            "execution_context": context(relative, function, executable, kind, task_contexts),
            "dynamic": executable is None,
        })
    return rows


def inventory(root=ROOT):
    root = Path(root)
    files, calls = {}, []
    for path in sources(root):
        calls.extend(inspect_source(path, root))
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    setup = root / "tools/setup_scoring_host.sh"
    if setup.is_file():
        files["tools/setup_scoring_host.sh"] = hashlib.sha256(setup.read_bytes()).hexdigest()
    clone = root / "tools/clone_verifier_checkout.sh"
    if clone.is_file():
        files["tools/clone_verifier_checkout.sh"] = hashlib.sha256(clone.read_bytes()).hexdigest()
    for name in ("judge/host-runtime.lock.json", "judge/requirements-interface.lock",
                 "judge/requirements-host-registry.lock"):
        path = root / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    task_commands = []
    for path in sorted((root / "tasks").glob("*/payload.json")):
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((root / "tasks").glob("*/tests/judge.json")):
        value = json.loads(path.read_text())
        def walk(item, keys=()):
            if isinstance(item, dict):
                for key, val in item.items():
                    if key in ("command", "compile", "interpreter", "python"):
                        binary = val[0] if isinstance(val, list) and val and isinstance(val[0], str) else val if isinstance(val, str) else None
                        if binary:
                            task_commands.append({
                                "task": path.parent.parent.name, "field": ".".join((*keys, key)),
                                "binary_or_recipe": binary if len(binary) < 180 and re.fullmatch(r"[A-Za-z0-9_./+-]+", binary) else "<dynamic>",
                                "execution_context": "pinned-worker",
                            })
                    walk(val, (*keys, key))
            elif isinstance(item, list):
                for val in item:
                    walk(val, keys)
        walk(value)
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema": "optimbench-external-command-audit-v1",
        "argv_identity_scheme": "exact-source-segment-utf8-lf-v2",
        "host_dependencies": HOST,
        "source_sha256": files, "process_sites_and_producers": calls,
        "configured_worker_commands": task_commands,
        "dynamic_boundary": "Step/config/recipe argv, image compilers and candidate descendants are not host package requirements. They remain explicit dynamic sites and must be exercised in pinned-image readiness gates; do not count this audit as their successful execution.",
        "direct_compiler_mode": "judge.c_preprocessor may use cc/c++/gcc/clang variants on host only when explicitly invoked without an image; scored judge paths supply the pinned image. Install build-essential or the declared exact compiler for an intentional direct-mode check, never substitute it for task image pins.",
    }


def check(root=ROOT, manifest=None):
    path = Path(manifest) if manifest else Path(root) / "tools/judge_external_commands.json"
    if not path.is_file():
        raise ValueError("External-command audit missing. Review launch sites, then run python3 -m tools.judge_command_audit --write --reviewed")
    expected, current = json.loads(path.read_text()), inventory(root)
    if expected != current:
        raise ValueError("Judge/adapters/external-command producers changed since reviewed audit. Run python3 -m tools.judge_command_audit --check, review host/worker dependencies, then python3 -m tools.judge_command_audit --write --reviewed at the integrated frozen head")
    return {"sources": len(current["source_sha256"]), "launch_sites_and_producers": len(current["process_sites_and_producers"]),
            "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--reviewed", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        if not args.reviewed:
            parser.error("--write requires --reviewed; never regenerate automatically in preflight")
        MANIFEST.write_text(json.dumps(inventory(), sort_keys=True, indent=2) + "\n")
    try:
        print(json.dumps(check(), sort_keys=True))
    except ValueError as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
