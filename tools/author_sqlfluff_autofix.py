#!/usr/bin/env python3
"""Author new SQLFluff cases from pinned originals, without opening old inputs.

Plans and originals for held-out cases are evaluator-owned. This command emits
only aggregate status. Run it outside the optimizing agent environment.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent / "runner"))
from runner import canonical_json, contained, inventory_sha256

TASK = Path(__file__).resolve().parents[1] / "tasks/sqlfluff-project-lint"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def original_url(plan, row):
    if "origins" in plan:
        origin = next(item for item in plan["origins"] if item["prefix"] == row["origin"])
        original = row["original_path"]
        if row["path"] != origin["prefix"] + "/" + original:
            raise ValueError("Origin namespace mismatch")
    else:
        origin, original = plan, row["path"]
    member = Path(original)
    if member.is_absolute() or ".." in member.parts or member.as_posix() != original:
        raise ValueError("Noncanonical original member path")
    if not re.fullmatch(r"[0-9a-f]{40}", origin["revision"]):
        raise ValueError("Inputs require a full source revision")
    return (f"https://raw.githubusercontent.com/{origin['repository']}/"
            f"{origin['revision']}/{urllib.parse.quote(original)}")


def authenticate(plan, originals):
    required = {row["path"]: row for row in plan["files"]}
    if len(required) != len(plan["files"]):
        raise ValueError("Duplicate original input declaration")
    actual = {p.relative_to(originals).as_posix() for p in originals.rglob("*") if p.is_file()}
    if actual != set(required) or any(p.is_symlink() for p in originals.rglob("*")):
        raise ValueError("Original inventory or file type mismatch")
    if not plan["targets"] or len(plan["targets"]) != len(set(plan["targets"])):
        raise ValueError("Missing or duplicate SQL targets")
    for name, row in required.items():
        raw = contained(originals, name).read_bytes()
        if len(raw) != row["bytes"] or digest(raw) != row["sha256"]:
            raise ValueError("Original input authentication failed")
        if row["url"] != original_url(plan, row):
            raise ValueError("Inputs require revision-pinned original URLs")
    if any(name not in required or not name.lower().endswith(".sql") for name in plan["targets"]):
        raise ValueError("A target is not an authenticated original SQL file")
    if not plan["notices"] or any(name not in required for name in plan["notices"]):
        raise ValueError("Original license notices must be included")
    if len({required[name]["sha256"] for name in plan["targets"]}) != len(plan["targets"]):
        raise ValueError("Duplicate SQL input bytes")
    if "origins" in plan:
        origins = plan["origins"]
        prefixes = {origin["prefix"] for origin in origins}
        if (len(origins) < 2 or len(prefixes) != len(origins)
                or len({origin["repository"].casefold() for origin in origins}) != len(origins)
                or {row["origin"] for row in plan["files"]} != prefixes):
            raise ValueError("Origin groups must be distinct and populated")
        for prefix in prefixes:
            if (not re.fullmatch(r"origin-[1-9][0-9]*", prefix)
                    or not any(name.startswith(prefix + "/") for name in plan["targets"])
                    or not any(name.startswith(prefix + "/") for name in plan["notices"])):
                raise ValueError("Each origin needs original SQL and a license notice")
    return required


def validate_reference(plan, originals, reference, receipt_path):
    raw = reference.read_bytes()
    result, receipt = json.loads(raw), read(receipt_path)
    if set(result) != {"format", "exit_code", "stdout_base64", "stderr_base64",
                       "files", "filesystem_violations"}:
        raise ValueError("Reference schema is incomplete")
    if (result["format"] != "sqlfluff-project-autofix-v1"
            or type(result["exit_code"]) is not int or result["exit_code"] not in (0, 1)
            or result["filesystem_violations"] != []):
        raise ValueError("Reference is not a completed valid fix operation")
    for stream in ("stdout_base64", "stderr_base64"):
        base64.b64decode(result[stream], validate=True)
    paths = {".": originals, **{p.relative_to(originals).as_posix(): p
                                for p in originals.rglob("*")}}
    if set(result["files"]) != set(paths):
        raise ValueError("Reference inventory does not match the original inputs")
    changed, changed_origins = 0, set()
    for name, path in paths.items():
        row = result["files"][name]
        kind = "directory" if path.is_dir() else "file"
        required = {"kind", "mode"} | ({"nlink", "content_base64"} if kind == "file" else set())
        if (set(row) != required or row["kind"] != kind
                or row["mode"] != (0o777 if kind == "directory" else 0o666)
                or (kind == "file" and (type(row["nlink"]) is not int or row["nlink"] < 1))):
            raise ValueError("Reference file observation is incomplete")
        if kind == "file":
            final = base64.b64decode(row["content_base64"], validate=True)
            different = final != path.read_bytes()
            if different and name not in plan["targets"]:
                raise ValueError("Reference changed a non-target input")
            changed += different
            if different:
                changed_origins.add(name.split("/")[0])
    if not changed:
        raise ValueError("Auto-fix reference must contain a genuine target rewrite")
    if "origins" in plan and changed_origins != {row["prefix"] for row in plan["origins"]}:
        raise ValueError("Every source origin must contain a genuine target rewrite")
    expected = {
        "input_inventory_sha256": inventory_sha256(originals),
        "reference_sha256": digest(raw),
        "collector_sha256": digest((TASK / "environment/workload/driver/autofix.py").read_bytes()),
        "upstream_commit": "0348222a3bcdb0226563f7349c5a39beb0641955",
        "pristine_python_sources_match": True,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("Reference provenance does not match the inputs and collector")
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt.get("image_id", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", receipt.get("build_sha256", ""))):
        raise ValueError("Reference requires a captured image and build identity")
    return receipt


def acquire(plan_path, originals):
    """Reacquire only the frozen, original public-source bytes in a private plan."""
    plan = read(plan_path)
    if originals.exists():
        raise ValueError("Acquisition requires a fresh destination")
    originals.mkdir(parents=True)
    for row in plan["files"]:
        if row["url"] != original_url(plan, row):
            raise ValueError("Unpinned acquisition URL")
        path = contained(originals, row["path"])
        with urllib.request.urlopen(row["url"], timeout=120) as response:
            raw = response.read(row["bytes"] + 1)
        if len(raw) != row["bytes"] or digest(raw) != row["sha256"]:
            raise ValueError("Acquisition size or digest mismatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    authenticate(plan, originals)
    return {"authenticated_files": len(plan["files"]), "input_transformations": 0}


def _stage_author(scope, plan_path, originals, reference, task, receipt, replace_draft):
    TASK = task
    plan = read(plan_path)
    plan["reference_provenance"] = receipt
    files = authenticate(plan, originals)
    public = scope == "public"
    case_id = "project-autofix-public" if public else "project-autofix-evaluation"
    project = "autofix-public" if public else "autofix-evaluation"
    inputs = TASK / ("environment/workload/inputs" if public else "tests/heldout")
    manifest_path = inputs / "manifest.json"
    manifest = read(manifest_path)
    existing_docs = [doc for batches in manifest["splits"].values()
                     for batch in batches for doc in batch["docs"]]
    previous = [doc for doc in existing_docs if doc["path"] == case_id]
    if previous and not replace_draft:
        raise ValueError("Case already exists. Frozen originals cannot be silently replaced")
    if previous:
        if not public or not manifest["edition"].endswith("-draft") or len(previous) != 1:
            raise ValueError("Only this unpublished public draft case may be replaced")
        for batches in manifest["splits"].values():
            batches[:] = [batch for batch in batches if batch["docs"] != previous]
        if any(doc["path"] == case_id for batches in manifest["splits"].values()
               for batch in batches for doc in batch["docs"]):
            raise ValueError("Cannot replace a mixed existing batch")
    old_manifest = copy.deepcopy(manifest)
    descriptor = {
        "operation": "autofix", "project": project,
        "inventory_sha256": inventory_sha256(originals),
        "targets": plan["targets"], "timeout": 180,
    }
    raw = canonical_json(descriptor)
    source_hash = digest(raw)
    source_path = inputs / "files" / source_hash
    expected = reference.read_bytes()
    result = json.loads(expected)
    if (result.get("format") != "sqlfluff-project-autofix-v1"
            or result.get("filesystem_violations") != []
            or not result.get("files")):
        raise ValueError("Reference must be a complete, valid original auto-fix result")
    if public:
        workloads_path = TASK / "environment/public-workloads.lock.json"
        workloads = read(workloads_path)
        if any(case["id"] == project for case in workloads["cases"]) and not replace_draft:
            raise ValueError("Public source already declared")
        workloads["cases"] = [case for case in workloads["cases"] if case["id"] != project]
    elif (inputs / "projects" / project).exists():
        raise ValueError("Private original project already exists")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(raw)
    document = {
        "path": case_id, "bytes": len(raw), "sha256": source_hash,
        "expect_len": len(expected), "expect_sha": digest(expected),
    }
    split = next(iter(manifest["splits"]))
    manifest["splits"][split].append({"docs": [document]})
    manifest["edition"] = "v1.0-autofix-draft"
    # New additions must leave every original descriptor/reference untouched.
    assert manifest["splits"][split][:-1] == old_manifest["splits"][split]
    for name in old_manifest["splits"]:
        if name != split:
            assert manifest["splits"][name] == old_manifest["splits"][name]
    write(manifest_path, manifest)
    payload = read(TASK / "payload.json")
    if previous:
        previous_destination = f"environment/workload/inputs/files/{previous[0]['sha256']}"
        payload["files"] = [row for row in payload["files"] if row["destination"] != previous_destination]
    additions = [source_path]
    if public:
        workloads["cases"].append({
            "id": project, "path": project,
            "files_sha256": {name: row["sha256"] for name, row in files.items()},
            "symlinks": {},
            "source": {**({"origins": plan["origins"]} if "origins" in plan else {
                "repo": plan["repository"], "commit": plan["revision"]}),
                "files": plan["files"], "license": plan.get("license", "See per-origin notices"),
                "selection": plan["eligibility"], "reference_provenance": receipt},
        })
        write(workloads_path, workloads)
        for name in plan["notices"]:
            destination = TASK / "environment/notices/autofix" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(contained(originals, name), destination)
    else:
        # Only new original files are copied. No old private body is opened.
        project_root = inputs / "projects" / project
        shutil.copytree(originals, project_root)
        additions.extend(p for p in project_root.rglob("*") if p.is_file())
        source_manifest = inputs / "autofix-sources.json"
        write(source_manifest, plan)
        additions.append(source_manifest)
        output = inputs / "expected/autofix-v1.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(expected)
        additions.append(output)
        lock_path = TASK / "tests/heldout.lock.json"
        lock = read(lock_path)
        for path in [manifest_path, *additions]:
            lock["files"][path.relative_to(TASK / "tests").as_posix()] = digest(path.read_bytes())
        write(lock_path, lock)
    for path in additions:
        body = path.read_bytes()
        body_directory = path.relative_to(inputs).parts[0]
        payload["files"].append({
            "scope": scope, "name": digest(body),
            "destination": path.relative_to(TASK).as_posix(),
            "bytes": len(body), "sha256": digest(body),
            "distribution": "lfs" if not public and body_directory in (
                "files", "projects", "expected") else "git",
        })
    # Preserve and rebind the old manifest entry without revealing its records.
    for row in payload["files"]:
        if row["destination"] == manifest_path.relative_to(TASK).as_posix():
            row.update(bytes=manifest_path.stat().st_size, sha256=digest(manifest_path.read_bytes()))
    write(TASK / "payload.json", payload)
    if public:
        write(TASK / "environment/payload-public.json",
              {"schema_version": 1, "task": TASK.name, "files": [
                  {**row, "destination": row["destination"].removeprefix("environment/")}
                  for row in payload["files"] if row["scope"] == "public"]})
    return {"scope": scope, "sql_files": len(plan["targets"]), "original_files": len(files),
            "reference_frozen": True, "existing_cases_unchanged": True,
            "qualified": False}


def commit_staged(staged, destination):
    """Prepare every byte before publishing, and roll back a failed commit."""
    with tempfile.TemporaryDirectory(prefix=".autofix-backup-", dir=destination.parent) as temporary:
        backups, applied, created = {}, [], []
        files = sorted(path for path in staged.rglob("*") if path.is_file())
        for index, source in enumerate(files):
            target = destination / source.relative_to(staged)
            if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                raise ValueError("Authoring destinations cannot traverse symbolic links")
            if target.exists():
                backup = Path(temporary) / str(index)
                shutil.copy2(target, backup)
                backups[target] = backup
        try:
            for source in files:
                target = destination / source.relative_to(staged)
                missing, parent = [], target.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for parent in reversed(missing):
                    parent.mkdir()
                    created.append(parent)
                os.replace(source, target)
                applied.append(target)
        except BaseException:
            for target in reversed(applied):
                if target in backups:
                    os.replace(backups[target], target)
                else:
                    target.unlink()
            for directory in reversed(created):
                directory.rmdir()
            raise


def author(scope, plan_path, originals, reference, receipt_path=None, replace_draft=False):
    plan = read(plan_path)
    authenticate(plan, originals)
    receipt = validate_reference(plan, originals, reference,
                                 receipt_path or reference.parent / "reference-provenance.json")
    public = scope == "public"
    metadata = ["payload.json"]
    metadata += (["environment/workload/inputs/manifest.json", "environment/public-workloads.lock.json"]
                 if public else ["tests/heldout/manifest.json", "tests/heldout.lock.json"])
    if not public and any((TASK / name).exists() for name in (
            "tests/heldout/projects/autofix-evaluation",
            "tests/heldout/autofix-sources.json", "tests/heldout/expected/autofix-v1.json")):
        raise ValueError("Private addition already exists")
    with tempfile.TemporaryDirectory(prefix=".autofix-stage-", dir=TASK.parent) as temporary:
        stage = Path(temporary) / TASK.name
        for name in metadata:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TASK / name, target)
        result = _stage_author(scope, plan_path, originals, reference, stage, receipt, replace_draft)
        commit_staged(stage, TASK)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("public", "heldout"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--originals", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--acquire-only", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--replace-public-draft", action="store_true")
    args = parser.parse_args()
    if args.acquire_only:
        result = acquire(args.plan, args.originals)
    else:
        if args.scope is None or args.reference is None:
            parser.error("authoring requires --scope and --reference")
        result = author(args.scope, args.plan, args.originals, args.reference,
                        args.receipt, args.replace_public_draft)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
