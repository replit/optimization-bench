"""Offline release/transport preflight. This does NOT execute or qualify task judges."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def generic_registrations(task):
    """Registration is necessary, never proof that a native gate will pass."""
    from judge.fuzzing_generic import task_runners
    callbacks, hooks = task_runners(task)
    required = ("stream_faults", "repeat_in_process", "aliasing")
    missing = [name for name in required if not callable(callbacks.get(name))]
    missing += [name for name in ("CLI_CASES", "HEADER_CONSUMERS")
                if not callable(getattr(hooks, name, None))]
    settings = read(task / "tests/judge.json")
    state = settings.get("process_state", {})
    if not isinstance(state, dict) or state.get("enabled") is not True or not state.get("kind"):
        missing.append("process_state")
    if missing:
        raise ValueError("Generic qualification integration missing: " + ", ".join(missing))
    return {"status": "registered", "native_execution": "not_run",
            "scope": "callback presence only; applicability and verdicts require native qualification"}


def inspect_task(root, task_id, version, require_payloads=False, require_generic_hooks=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]+", task_id):
        raise ValueError("Invalid task ID")
    task = root / "tasks" / task_id
    release, definition = read(task / "release.json"), read(task / "task.json")
    config = tomllib.loads((task / "task.toml").read_text())
    package_version = config["task"]["version"]
    if {release.get("task_version", package_version),
            definition.get("task_version", package_version), package_version} != {version}:
        raise ValueError("Task version metadata does not match the selected release")
    rules = release["source_policy"]
    file_exports = (rules.get("operations") == ["modify"] and
                    rules["source_roots"] == ["."] and bool(rules.get("allowed_files")))
    exports = rules["allowed_files"] if file_exports else rules["source_roots"]
    if not exports or len(exports) != len(set(exports)):
        raise ValueError("Missing or duplicate artifact exports")
    sources = [a["source"] for a in config["artifacts"]]
    if len(sources) != len(set(sources)) or len(sources) != len(exports):
        raise ValueError("Harbor artifact count differs from the source policy")
    bases = None
    for export in exports:
        if Path(export).is_absolute() or ".." in Path(export).parts:
            raise ValueError("Unsafe source policy export")
        candidates = {source if export == "." else source[:-len("/" + export)]
                      for source in sources if export == "." or source.endswith("/" + export)}
        bases = candidates if bases is None else bases & candidates
    if len(bases or ()) != 1:
        raise ValueError("Harbor exports do not share a unique declared source root")
    settings = read(task / "tests/judge.json")
    from judge.sampling_policy import resolve
    sampling = resolve(settings)
    registration = generic_registrations(task) if require_generic_hooks else {"status": "not_checked"}
    # Report only explicit plans; implicit cases require the task's own smoke test.
    units = {scope: list(plan) for scope, plan in settings.get("performance_units", {}).items()}
    files = ["task.toml", "task.json", "release.json", "tests/judge.json",
             "tests/heldout.lock.json", "payload.json"]
    identity = {name: hashlib.sha256((task / name).read_bytes()).hexdigest() for name in files}
    if require_payloads:
        from judge.public_input_preflight import ensure_public_inputs
        ensure_public_inputs(task, fetch=False, root=root)
        lock = read(task / "tests/heldout.lock.json")
        for name, expected in lock["files"].items():
            path = task / "tests" / name
            if not path.resolve().is_relative_to((task / "tests").resolve()):
                raise ValueError("Held-out input escapes the task")
            actual = None
            if path.is_file():
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("Held-out payload missing or unauthenticated: " + name)
    return {"task": task_id, "task_version": version, "status": "passed",
            "artifact_kind": "files" if file_exports else "directories",
            "exports": exports, "declared_performance_units": units,
            "input_bytes_verified": require_payloads, "definition_hashes": identity,
            "sampling_policy": sampling, "generic_registration": registration,
            "judge_smoke_test": "not_run", "timing_qualification": "not_checked"}


def inspect_release(root=ROOT, release="v1.0", tasks=None, require_payloads=False,
                    require_generic_hooks=False):
    root = Path(root)
    if release != "v1.0":
        raise ValueError("Unknown release")
    selected = read(root / "datasets" / (release + ".json"))
    members = selected["tasks"]
    if len(members) != len(set(members)):
        raise ValueError("Duplicate task membership")
    versions = selected.get("task_versions", {})
    if not isinstance(versions, dict) or not set(versions) <= set(members):
        raise ValueError("Task version overrides must belong to the selected release")
    tasks = tasks or members
    if not set(tasks) <= set(members):
        raise ValueError("Task is outside the selected release")
    rows = []
    for task in tasks:
        try:
            version = versions.get(task, selected.get("task_version", release[1:] + ".0"))
            rows.append(inspect_task(root, task, version,
                                     require_payloads, require_generic_hooks))
        except (ValueError, KeyError, OSError, TypeError) as error:
            rows.append({"task": task, "status": "blocked", "reason": str(error)})
    return {"release": selected["release"], "passed": all(r["status"] == "passed" for r in rows),
            "scope": "repository contracts and transport only; not judge or timing qualification",
            "tasks": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=["v1.0"], default="v1.0")
    parser.add_argument("--task", action="append")
    parser.add_argument("--require-payloads", action="store_true",
                        help="Authenticate locally staged public and private bytes; never download them")
    parser.add_argument("--require-generic-hooks", action="store_true",
                        help="Fail before provisioning if RC callbacks/process-state configuration are absent; does not execute or qualify gates")
    args = parser.parse_args()
    try:
        result = inspect_release(release=args.release, tasks=args.task, require_payloads=args.require_payloads,
                                 require_generic_hooks=args.require_generic_hooks)
    except (ValueError, KeyError, OSError) as error:
        result = {"passed": False, "reason": str(error)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
