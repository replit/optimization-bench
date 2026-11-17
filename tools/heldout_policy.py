"""Repository-only LFS, size and public/private separation checks.

The verifier's content-lock check deliberately does not require Git: verifier
images contain real bytes and no repository metadata.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

PUBLIC_FILE_LIMIT = 1_000_000
HEX = re.compile(rb"[a-f0-9]{64}")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def attributes(root, paths):
    paths = list(paths)
    if not paths:
        return {}
    raw = subprocess.check_output(
        ["git", "-C", str(root), "check-attr", "-z", "--stdin",
         "filter", "diff", "merge", "text"],
        input=b"".join(str(path).encode() + b"\0" for path in paths))
    fields = raw.decode().split("\0")[:-1]
    result = {}
    for name, key, value in zip(fields[::3], fields[1::3], fields[2::3]):
        result.setdefault(name, {})[key] = value
    return result


def expected_artifact(name):
    parts = Path(name).parts
    if parts[:1] == ("tests",):
        parts = parts[1:]
    return len(parts) >= 3 and parts[0] == "heldout" and parts[1] in ("expected", "references")


def output_cases(path):
    if not path.is_file():
        return []
    value = json.loads(path.read_text())
    if isinstance(value, list):
        return [(row["id"], row["sha256"]) for row in value
                if isinstance(row, dict) and "id" in row and "sha256" in row]
    return [(doc["path"], doc["expect_sha"])
            for batches in value.get("splits", {}).values()
            for batch in batches for doc in batch["docs"]
            if doc.get("expect_sha") and doc.get("path")]


def output_coincidences(tasks):
    public = {}
    tasks = list(tasks)
    for task in tasks:
        for path in (task / "environment/workload/inputs/manifest.json",
                     task / "environment/workload/baseline-outputs.json"):
            for case, sha in output_cases(path):
                public.setdefault(sha, set()).add((task.name, case))
    rows = []
    for task in tasks:
        private = set(output_cases(task / "tests/heldout/manifest.json"))
        private.update(output_cases(task / "tests/heldout/baseline-outputs.json"))
        for case, sha in sorted(private):
            for public_task, public_case in sorted(public.get(sha, ())):
                rows.append({
                    "message": f"expected-output coincides with public output: "
                               f"{task.name}/{case} == {public_task}/{public_case}",
                    "private_task": task.name, "private_case": case,
                    "public_task": public_task, "public_case": public_case,
                    "sha256": sha
                })
    return rows


def private_hashes(task):
    result = set()
    lock = json.loads((task / "tests/heldout.lock.json").read_text())
    for entry in lock.get("references", {}).values():
        if entry.get("kind") == "git-history":
            result.add(entry["object_inventory"]["sha256"])
            result.add(entry["checkout_inventory_sha256"])
        else:
            result.add(entry["sha256"])
    # Historical unavailable manifests are public placeholder metadata and
    # already appear in frozen public-artifact inventories. They are not data.
    result.update(digest for name, digest in lock["files"].items()
                  if not expected_artifact(name) and
                  (name != "heldout/manifest.json" or lock.get("status") == "available"))
    payload = json.loads((task / "payload.json").read_text())
    result.update(row["sha256"] for row in payload["files"]
                  if row["scope"] == "heldout" and row.get("sha256")
                  and not expected_artifact(row["destination"]))
    manifest = task / "tests/heldout/manifest.json"
    if manifest.is_file():
        value = json.loads(manifest.read_text())
        result.update(doc["sha256"] for batches in value.get("splits", {}).values()
                      for batch in batches for doc in batch["docs"])
    inputs_lock = task / "tests/heldout/inputs.lock.json"
    if inputs_lock.is_file():
        result.update(row["sha256"] for row in json.loads(inputs_lock.read_text())["files"].values())
    # An empty file conveys no held-out input information. This single exact
    # digest exemption is not a size threshold or a task-specific allowance.
    result.discard(EMPTY_SHA256)
    return result


def empty_input_paths(task):
    """Declared zero-byte inputs, excluding independently allowed expected outputs."""
    lock = json.loads((task / "tests/heldout.lock.json").read_text())
    names = {"tests/" + name for name, sha in lock["files"].items()
             if sha == EMPTY_SHA256 and not expected_artifact(name)}
    names.update("tests/" + name for name, entry in lock.get("references", {}).items()
                 if entry.get("sha256") == EMPTY_SHA256)
    payload = json.loads((task / "payload.json").read_text())
    names.update(row["destination"] for row in payload["files"]
                 if row["scope"] == "heldout" and row.get("sha256") == EMPTY_SHA256
                 and not expected_artifact(row["destination"]))
    manifest = task / "tests/heldout/manifest.json"
    if manifest.is_file():
        value = json.loads(manifest.read_text())
        names.update("tests/heldout/files/" + doc["sha256"]
                     for batches in value.get("splits", {}).values()
                     for batch in batches for doc in batch["docs"]
                     if doc["sha256"] == EMPTY_SHA256)
    inputs_lock = task / "tests/heldout/inputs.lock.json"
    if inputs_lock.is_file():
        names.update("tests/heldout/data/" + name for name, row in
                     json.loads(inputs_lock.read_text())["files"].items()
                     if row["sha256"] == EMPTY_SHA256)
    return {task / name for name in names}


def check_repository(root, tasks, report=None):
    """Cross-task disjointness, including bytes and digest references."""
    root = Path(root)
    tasks = list(tasks)
    secrets = set().union(*(private_hashes(task) for task in tasks))
    empty_paths = set().union(*(empty_input_paths(task) for task in tasks))
    exempt = {path.relative_to(root).as_posix() for path in empty_paths}
    bodies = {
        (task / row["destination"]).relative_to(root).as_posix()
        for task in tasks
        for row in json.loads((task / "payload.json").read_text())["files"]
        if row["scope"] == "public"
    }
    tracked = set(subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0"))
    paths = [path for task in tasks
             for folder in ("environment", "tests/heldout", "public")
             for path in (task / folder).rglob("*") if path.is_file()]
    from payload_check import repo_lfs_spec, verified
    recovered_by_task = {
        task: {repo_lfs_spec(row): row
               for row in json.loads((task / "payload.json").read_text())["files"]
               if row.get("kind") == "repo-lfs"}
        for task in tasks
    }
    attrs = attributes(root, [p.relative_to(root).as_posix() for p in paths])
    for path in paths:
        relative = path.relative_to(root).as_posix()
        attr = attrs[relative]
        public = path.relative_to(root).parts[2] in ("environment", "public")
        if public:
            task = root / Path(relative).parts[0] / Path(relative).parts[1]
            recovered = recovered_by_task[task]
            source_name = path.relative_to(task).as_posix()
            is_recovered = source_name in recovered
            if is_recovered:
                if attr != {"filter": "lfs", "diff": "lfs", "merge": "lfs", "text": "unset"}:
                    raise ValueError("repo-lfs public body must be LFS-tracked: " + relative)
                if not verified(path, recovered[source_name]):
                    raise ValueError("repo-lfs size/SHA-256 mismatch: " + relative)
            elif any(attr[key] == "lfs" for key in ("filter", "diff", "merge")):
                raise ValueError("LFS forbidden in agent environment: " + relative)
            if "heldout" in path.relative_to(root).parts[3:]:
                raise ValueError("Held-out path forbidden in agent environment: " + relative)
            # Size policy is for retained public input bodies, not existing
            # multi-MB pristine inventories and other package metadata.
            if not is_recovered and relative in bodies and relative in tracked and path.stat().st_size > PUBLIC_FILE_LIMIT:
                raise ValueError("Public Git payload exceeds 1 MB/file: " + relative)
            digest = hashlib.sha256()
            tail = b""
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    if secrets.intersection(m.decode() for m in HEX.findall(tail + chunk)):
                        raise ValueError("Held-out sha256 in public environment/manifest: " + relative)
                    tail = chunk[-63:]
            if digest.hexdigest() in secrets:
                raise ValueError("Held-out bytes in public environment: " + relative)
            if empty_paths and digest.hexdigest() == EMPTY_SHA256:
                exempt.add(relative)
        elif path.stat().st_size > PUBLIC_FILE_LIMIT:
            if attr != {"filter": "lfs", "diff": "lfs", "merge": "lfs", "text": "unset"}:
                raise ValueError("Held-out file over 1 MB must be LFS-tracked: " + relative)
    # A public record must never point outside the agent context; a private
    # record must never be staged there, even before its bytes are available.
    for task in tasks:
        payload = json.loads((task / "payload.json").read_text())
        for row in payload["files"]:
            prefix = "environment/" if row["scope"] == "public" else "tests/heldout/"
            if not row["destination"].startswith(prefix):
                raise ValueError("Payload scope/destination mismatch: " + row["destination"])
    if report is not None:
        report["exempt_empty_files"] = sorted(exempt)
    return output_coincidences(tasks)
