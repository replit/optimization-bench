#!/usr/bin/env python3
"""Check every task shell, content bindings, payload states and generated catalog."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
import subprocess

from stage_payload import ROOT, missing, contained, git_history_roots
from update_catalog import (
    generated, task_paths, unavailable_inputs, neutral_prompt_tasks,
    validate_neutral_prompt,
)
from heldout_policy import check_repository

REQUIRED = (
    "instruction.md", "instruction.md.njk", "README.md", "task.json", "task.toml", "release.json",
    "provenance.json", "qualification-status.json", "payload.json",
    "environment/Dockerfile", "environment/README.md", "environment/source.lock.json",
    "environment/pristine-files.lock.json", "environment/public-workloads.lock.json",
    "environment/payload-public.json", "environment/payload_check.py",
    "environment/notices/DISTRIBUTION.md",
    "environment/workload/task.py", "environment/workload/runner.py",
    "environment/workload/build.sh", "environment/workload/inputs/manifest.json",
    "tests/README.md", "tests/Dockerfile", "tests/test.sh", "tests/judge.json",
    "tests/heldout/manifest.json", "tests/heldout.lock.json",
)
OPTIONAL_NAMES = {
    "": {".gitattributes", ".gitignore", "notices", "public", "RESEARCH.md"},
    "environment": {".dockerignore", ".gitignore", "layout.json", "notices", "agent-tools",
        "docker-compose.yaml", "agent.Dockerfile", "png-inputs.lock.json",
        "prepare.py", "public-artifacts.json", "public-inputs.lock.json", "public-input-packet.json", "requirements-tests.lock",
        "requirements-toolchain.txt", "requirements.lock", "source-overlay", "source-policy.json",
        "toolchain-image.lock.json", "toolchain-packages.lock.json"},
    "environment/workload": {"baseline-outputs.json", "cases", "check_public.py", "compiler.py",
        "corpus.json", "driver", "fetch_inputs.py", "frame_pipeline.c", "guards", "layout.json",
        "manifest.json", "native.py", "native", "notices", "numpy_helpers.py", "interface_snapshot.py",
        "public-inputs.lock.json", "public-trees.json", "worker.c", "worker_launcher.py", "zrip.Cargo.lock"},
    "tests": {"api_guard.c", "acquire_heldout.py", "test_acquire_heldout.py",
        "baseline-outputs.json", "check_dev_outputs.py", "differential", "fuzz", "receipts", "notices",
        "affinity_diagnostic.py", "upstream-config.json",
        "upstream-expectations.json", "upstream.py"},
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_manifest_pins(task, payload):
    """Enforce package-manifest transport pins even for direct check_task callers."""
    manifests = {"environment/workload/inputs/manifest.json", "tests/heldout/manifest.json"}
    for entry in payload["files"]:
        name = entry.get("destination")
        if name not in manifests or entry.get("retention") == "fetch-only":
            continue
        path = contained(task, name)
        if (not path.is_file() or type(entry.get("bytes")) is not int
                or path.stat().st_size != entry["bytes"] or sha(path) != entry.get("sha256")):
            raise ValueError("Manifest payload size/SHA256 mismatch: " + task.name + "/" + name)
    lock_path = task / "tests/heldout.lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text())
        expected = lock.get("files", {}).get("heldout/manifest.json")
        if expected is not None:
            path = contained(task, "tests/heldout/manifest.json")
            if not path.is_file() or sha(path) != expected:
                raise ValueError("Manifest held-out lock SHA256 mismatch: " + task.name)


def guard_version(path, manifest):
    """Admit only released canonical bytes with all required capabilities."""
    registry = json.loads((ROOT / "tools/payload_guard_versions.json").read_text())
    assert registry.get("schema_version") == 1, "Unsupported public guard registry"
    versions, hashes = {}, {}
    for version in registry.get("versions", []):
        identity, digest, kinds = version.get("id"), version.get("sha256"), version.get("payload_kinds")
        assert isinstance(identity, str) and identity and identity not in versions, "Invalid guard version ID"
        assert isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) and digest not in hashes, "Invalid guard hash"
        assert (isinstance(kinds, list) and kinds and len(kinds) == len(set(kinds))
                and all(isinstance(kind, str) and kind for kind in kinds)), "Invalid guard capabilities"
        versions[identity], hashes[digest] = version, version
    assert registry.get("current") in versions, "Missing current canonical guard"
    current = versions[registry["current"]]
    assert current["sha256"] == sha(ROOT / "tools/payload_check.py"), "Current canonical guard is unregistered"
    digest = sha(path)
    assert digest in hashes, (str(path), "public guard drift: unknown canonical hash")
    version = hashes[digest]
    assert not version.get("revoked", False), (
        str(path), "canonical guard release revoked", version["id"], version.get("reason", ""))
    required = {record.get("kind", "file") for record in manifest.get("files", [])}
    unsupported = required - set(version["payload_kinds"])
    assert not unsupported, (str(path), "canonical guard does not support payload kinds", sorted(unsupported))
    recipes = set()
    def visit(node):
        if isinstance(node, dict):
            if isinstance(node.get("kind"), str):
                recipes.add(node["kind"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    for record in manifest.get("files", []):
        if record.get("scope", "public") == "public":
            visit(record.get("fetch"))
    if "gzip-member-slice" in recipes:
        assert "gzip-member-slice-v1" in version.get("features", []), (
            str(path), "canonical guard does not support gzip-member-slice")
    broken = recipes & set(version.get("unsupported_recipes", []))
    assert not broken, (str(path), "canonical guard release revoked for recipes", sorted(broken))
    older = version["id"] != registry["current"]
    return {"id": version["id"], "sha256": digest, "older_canonical": older,
            "message": "guard version: " + version["id"] +
                       (" (older canonical)" if older else " (current canonical)")}


def check_task(task, require_ready=False):
    sys.path.insert(0, str(ROOT))
    from tools.runner.preparation import metadata
    metadata(task)  # Optional hooks must be protected executable task code.
    fuzz = task / "tests/fuzz"
    if fuzz.exists() or fuzz.is_symlink():
        assert fuzz.is_dir() and not fuzz.is_symlink(), (task.name, "fuzz must be a verifier-owned directory")
        for item in fuzz.rglob("*"):
            assert not item.is_symlink() and item.resolve().is_relative_to(fuzz.resolve()), (
                task.name, "fuzz content must not escape verifier ownership", str(item))
    payload = json.loads((task / "payload.json").read_text())
    check_manifest_pins(task, payload)
    histories = git_history_roots(task, payload)
    declared = {root / ".git" for root in histories}
    if histories:
        tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"]).decode().split("\0")
        for root in histories:
            relative = root.relative_to(ROOT).as_posix()
            assert not any(path == relative or path.startswith(relative + "/") for path in tracked), (
                task.name, "Git history directory bodies are fetch-only, never retained in Git")
    for name in REQUIRED:
        assert (task / name).is_file(), (task.name, "missing shell file", name)
    for directory, optional in OPTIONAL_NAMES.items():
        shell = task / directory
        required_names = {Path(name).relative_to(directory).parts[0] if directory else Path(name).parts[0]
                          for name in REQUIRED if not directory or name.startswith(directory + "/")}
        # A retained, hash-authenticated public delivery is a declared payload,
        # not an undocumented helper directory. Permit only its exact members
        # and parents, never arbitrary siblings under the transport directory.
        public_files = [(task / entry["destination"]).resolve()
                        for entry in json.loads((task / "payload.json").read_text())["files"]
                        if entry["scope"] == "public" and entry.get("kind") != "git-history"]
        for destination in public_files:
            if destination.is_relative_to(shell.resolve()) and destination != shell.resolve():
                component = destination.relative_to(shell.resolve()).parts[0]
                if component not in optional | required_names:
                    parent = shell / component
                    for child in parent.rglob("*") if parent.is_dir() else [parent]:
                        assert not child.is_symlink(), (task.name, "Symlink in declared file payload transport")
                        assert any(child.resolve() == declared or declared.is_relative_to(child.resolve())
                                   for declared in public_files), (
                            task.name, "Undeclared file payload sibling", str(child))
                    optional = optional | {component}
        required_names = {Path(name).relative_to(directory).parts[0] if directory else Path(name).parts[0]
                          for name in REQUIRED if not directory or name.startswith(directory + "/")}
        for root in histories:
            if root.is_relative_to(shell) and root != shell:
                component = root.relative_to(shell).parts[0]
                if component not in optional | required_names:
                    parent = shell / component
                    for child in parent.rglob("*") if parent.exists() else []:
                        assert any(child.is_relative_to(h) or h.is_relative_to(child) for h in histories), (
                            task.name, "Undeclared directory payload sibling", str(child))
                optional = optional | {component}
        required_names = {Path(name).relative_to(directory).parts[0] if directory else Path(name).parts[0]
                          for name in REQUIRED if not directory or name.startswith(directory + "/")}
        actual = {path.name for path in (task / directory).iterdir() if path.name != "__pycache__"}
        assert actual <= required_names | optional, (
            task.name, "undocumented shell entry", directory, sorted(actual - required_names - optional))
    sidecar = task / "tests/baseline-outputs.json"
    if sidecar.exists() or sidecar.is_symlink():
        lock = json.loads((task / "tests/heldout.lock.json").read_text())
        assert (sidecar.is_file() and not sidecar.is_symlink()
                and lock.get("files", {}).get("baseline-outputs.json") == sha(sidecar)), (
            task.name, "verifier baseline-outputs metadata must be content-locked")
    assert set(task.rglob(".git")) <= declared, (task.name, "undeclared nested Git metadata")
    environment = task / "environment"
    for path in environment.rglob("*"):
        if any(path.is_relative_to(root) for root in histories):
            continue  # Authenticated input bytes, not executable package glue.
        if path.is_symlink():
            assert path.resolve().is_relative_to(environment.resolve()), (task.name, "escaping symlink")
        if not path.is_file() or path.suffix not in (".py", ".sh", ".json", ".toml", ".md", ""):
            continue
        # Upstream tests inside source overlays are public; explicit verifier paths are not.
        if "inputs" in path.relative_to(environment).parts:
            continue
        text = path.read_text(errors="replace")
        assert not re.search(r'(?:\.\./tests/|/opt/verifier|/tests/heldout|tasks/[^\s"\']+/tests/)', text), (
            task.name, "agent environment references verifier", str(path))
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["schema_version"] == "1.4", task.name
    assert config["verifier"]["environment_mode"] == "separate", task.name
    assert config["environment"]["cpus"] == 8, task.name
    assert config["agent"]["timeout_sec"] == 7200, (task.name, "agent budget")
    verifier_docker = (task / "tests/Dockerfile").read_text()
    assert "requirements-interface.lock" in verifier_docker and "/opt/verifier/vendor" in verifier_docker, (
        task.name, "verifier must install hash-locked interface parsers")
    interface = json.loads((task / "tests/judge.json").read_text())["public_interface"]
    assert interface["kind"] in ("binary", "composite", "symbols", "declarations", "python", "javascript"), task.name
    preparation = (environment / "Dockerfile").read_text()
    if (environment / "prepare.py").exists():
        preparation += (environment / "prepare.py").read_text()
    assert "init.defaultBranch=main" not in preparation, (task.name, "ambiguous baseline branch")
    assert "init.defaultBranch=optimbench-baseline" in preparation, task.name
    definition = json.loads((task / "task.json").read_text())
    assert definition["id"] == task.name
    for component in interface.get("components", [interface]):
        if component["kind"] == "python":
            from judge.interfaces import python_interface_config
            python_interface_config(task, component["packages"])
    assert (environment / "workload/runner.py").read_bytes() == (ROOT / "tools/runner/runner.py").read_bytes(), (
        task.name, "shared runner drift")
    guard = guard_version(environment / "payload_check.py", payload)
    adapter = ast.parse((environment / "workload/task.py").read_text())
    names = {node.name for node in adapter.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    names |= {target.id for node in adapter.body if isinstance(node, ast.Assign)
              for target in node.targets if isinstance(target, ast.Name)}
    assert {"ENV", "cases", "prepare", "collect"} <= names, (task.name, "adapter interface")
    assert not (environment / "workload/driver/run.py").exists(), (task.name, "per-task runner")
    pins = config["metadata"]["optimbench"]
    docker = (environment / "Dockerfile").read_text()
    for field in ("baseline_git_tree", "baseline_git_commit"):
        assert re.fullmatch("[a-f0-9]{40}", pins[field]), (task.name, field)
        assert pins[field] in docker, (task.name, "unasserted Git pin", field)
    assert pins["baseline_git_date"] == "2000-01-01T00:00:00+0000", task.name
    assert "payload_check.py" in docker, (task.name, "missing early payload gate")
    payload = json.loads((task / "payload.json").read_text())
    assert payload["schema_version"] == 1 and payload["task"] == task.name
    destinations = set()
    for record in payload["files"]:
        contained(task, record["destination"])
        assert record["destination"] not in destinations, (task.name, "duplicate payload")
        destinations.add(record["destination"])
        assert record["scope"] in ("public", "heldout")
        if record.get("kind") == "git-history":
            continue  # Canonical directory pins were validated by git_history_roots.
        assert record["sha256"] is None or re.fullmatch("[a-f0-9]{64}", record["sha256"])
        if record["sha256"] is None:
            assert record["scope"] == "heldout" and record.get("reason")
        if record["scope"] == "public":
            assert record["sha256"] is not None and isinstance(record["bytes"], int)
    public_subset = json.loads((environment / "payload-public.json").read_text())
    assert public_subset == {"schema_version": 1, "task": task.name, "files": [
        {**record, "destination": record["destination"].removeprefix("environment/")}
        for record in payload["files"] if record["scope"] == "public"]}
    public = missing(task, "public", allow_fetch=True)
    private = missing(task, "heldout")
    expected = "public-payload-missing" if public else "heldout-missing" if private else "ready"
    status = json.loads((task / "qualification-status.json").read_text())
    assert status["payload_status"] == expected, (task.name, "stale payload status")
    if require_ready:
        assert not public, (task.name, "PAYLOAD PENDING; tools/stage_payload.py")
    sys.path.insert(0, str(ROOT))
    from judge.heldout import check
    check(task / "tests", allow_missing_references=True)
    provenance = json.loads((task / "provenance.json").read_text())
    for name, digest in provenance.get("current_files_sha256", {}).items():
        assert sha(contained(task, name)) == digest, (task.name, "current hash binding", name)
    assert provenance.get("current_files_sha256"), (task.name, "no current content bindings")
    # Protected upstream/project inventories must never be "refreshed" from
    # package files that happen to share a basename.
    upstream_lock = json.loads((environment / "source.lock.json").read_text())
    for field in ("pristine_lock", "public_artifacts"):
        if field in upstream_lock:
            path = environment / upstream_lock[field]
            assert path.is_file(), (task.name, "broken metadata pointer", field)
            digest = upstream_lock.get(field + "_sha256")
            if digest:
                assert sha(path) == digest, (task.name, "metadata commitment", field)
    return {"task": task.name, "shell_complete": True, "payload_status": expected, "guard_version": guard,
            "public_missing_files": len(public), "heldout_missing_files": len(private)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/all.json")
    parser.add_argument("--task", action="append")
    parser.add_argument("--require-ready", action="store_true", help="Require complete PUBLIC payloads, not scored qualification")
    args = parser.parse_args()
    selected = args.task or json.loads(args.dataset.read_text())["tasks"]
    assert len(selected) == len(set(selected)), "Duplicate selection"
    selected_paths = task_paths(selected)
    for task in selected_paths:
        if task.name in neutral_prompt_tasks():
            validate_neutral_prompt(task)
    for path, text in generated(selected_paths).items():
        assert path.exists() and path.read_text() == text, (
            "Stale task metadata: run python3 tools/update_catalog.py --task " +
            next(task.name for task in selected_paths if path.is_relative_to(task)), str(path.relative_to(ROOT)))
    # Retain cross-task leak checks across materialized packages. Unavailable
    # transports are explicit partial-audit evidence, never successful tasks.
    repository_tasks = task_paths()
    unavailable = {path.name: unavailable_inputs(path) for path in repository_tasks}
    unavailable = {name: rows for name, rows in unavailable.items() if rows}
    available = [path for path in repository_tasks if path.name not in unavailable]
    disjointness = {}
    coincidences = check_repository(ROOT, available, report=disjointness) if available else []
    disjointness.update(complete=not unavailable, unavailable_tasks=sorted(unavailable),
                        scope="all materialized tasks; unavailable task safety is not certified")
    rows = []
    for task in selected_paths:
        if task.name in unavailable:
            rows.append({"task": task.name, "status": "unavailable",
                         "unavailable_inputs": unavailable[task.name], "scored_qualification": False})
        else:
            rows.append(check_task(task, args.require_ready))
    for row in rows:
        matches = [item for item in coincidences if item["private_task"] == row["task"]]
        if matches:
            row["expected_output_coincidences"] = matches
    print(json.dumps({"tasks": rows, "task_count": len(rows), "disjointness": disjointness, "library_executions": 0,
                      "scored_qualification": False}, indent=2))
    if args.require_ready and any(any(item["scope"] in ("public", "metadata") for item in unavailable.get(task.name, []))
                                  for task in selected_paths):
        parser.exit(2, "Selected public payload unavailable; install git-lfs and recover only its declared public paths.\n")
    if any(task.name in unavailable for task in selected_paths):
        parser.exit(2, "Selected task validation unavailable; recover only its authorized retained LFS/operator bodies. This is not a candidate failure.\n")


if __name__ == "__main__":
    main()
