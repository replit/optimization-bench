#!/usr/bin/env python3
"""Authenticate and stage task payloads; never invent missing expected outputs."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def reference_entries(task, manifest=None):
    """Require payload/lock agreement; reference bodies must never be retained."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from judge.heldout import references, reference_payload
    task = Path(task)
    lock_path = task / "tests/heldout.lock.json"
    lock = json.loads(lock_path.read_text()) if lock_path.is_file() else {"files": {}}
    rows = references(lock, task / "tests")
    manifest = manifest or json.loads((task / "payload.json").read_text())
    declared = reference_payload(rows, manifest)
    for name in rows:
        path = contained(task, "tests/" + name)
        if path.exists() or path.is_symlink():
            raise ValueError("Fetch-only reference body retained in worktree")
    return declared


def contained(root, name):
    root = Path(root).resolve()
    relative = Path(name)
    target = root / relative
    if relative.is_absolute() or ".." in relative.parts or not target.resolve().is_relative_to(root):
        raise ValueError("Payload path escapes task")
    for component in [target, *target.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("Symlink in payload path")
    return target


def authentic(path, entry):
    if entry.get("kind") == "repo-lfs":
        from payload_check import verified
        return verified(path, entry)
    if entry.get("scope") == "heldout":
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from judge.heldout import reject_lfs_pointer
        reject_lfs_pointer(path)
    if entry.get("kind") == "git-history":
        from payload_check import verified
        return verified(path, entry)
    if not path.is_file() or path.is_symlink():
        return False
    if entry.get("bytes") is not None and path.stat().st_size != entry["bytes"]:
        return False
    if not entry.get("sha256"):
        return False
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return digest == entry["sha256"]


def authentic_destination(row):
    """Process-pool entry point: authenticate one manifest entry in `task`."""
    task, entry = row
    return authentic(contained(task, entry["destination"]), entry)


def git_history_roots(task, manifest=None):
    """Validate explicit, fetch-only directory declarations before filesystem use."""
    from payload_check import git_history_spec
    task = Path(task)
    manifest = manifest or json.loads((task / "payload.json").read_text())
    private = reference_entries(task, manifest)
    roots = []
    for entry in manifest["files"]:
        if entry.get("kind", "file") not in ("file", "git-history", "repo-lfs"):
            raise ValueError("Unknown payload kind")
        if entry.get("kind") == "repo-lfs":
            from payload_check import repo_lfs_spec
            contained(task, repo_lfs_spec(entry))
            if not entry["destination"].startswith("environment/"):
                raise ValueError("repo-lfs destination must be inside environment/")
        if entry.get("kind") != "git-history":
            continue
        if entry["destination"] in private:
            continue  # Authenticated by the verifier outside the repository.
        git_history_spec(entry)
        if Path(entry["destination"]).parts[0] != "environment":
            raise ValueError("Public Git histories must be inside the environment")
        if entry["destination"] in {"environment", "environment/workload",
                                    "environment/workload/inputs", "environment/notices"}:
            raise ValueError("Git history cannot replace package glue directories")
        root = contained(task, entry["destination"])
        for other in manifest["files"]:
            if other is entry:
                continue
            path = contained(task, other["destination"])
            if root.is_relative_to(path) or path.is_relative_to(root):
                raise ValueError("Overlapping directory/file payload declarations")
        roots.append(root)
    return roots


def missing(task, scope=None, allow_fetch=False, allow_unfetched_private=False):
    manifest = json.loads((task / "payload.json").read_text())
    git_history_roots(task, manifest)
    private = reference_entries(task, manifest)
    from judge.heldout import concurrent_outcomes, outcome
    # Authenticate independent Git histories concurrently; the loop below still
    # consumes each result in manifest order, where it previously made the call.
    histories = [entry for entry in manifest["files"] if entry.get("kind") == "git-history"
                 and (scope is None or entry["scope"] == scope) and entry["destination"] not in private]
    verified = dict(zip(map(id, histories), concurrent_outcomes(
        authentic_destination, [(task, entry) for entry in histories])))
    absent = []
    for entry in manifest["files"]:
        if scope is not None and entry["scope"] != scope:
            continue
        if entry["destination"] in private:
            if not allow_fetch:
                absent.append(entry)
            continue
        target = contained(task, entry["destination"])
        if allow_unfetched_private and entry["scope"] == "heldout" and target.is_file():
            with target.open("rb") as stream:
                if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                    absent.append(entry)
                    continue
        if entry.get("kind") == "repo-lfs" and allow_fetch:
            from payload_check import repo_lfs_spec
            source = contained(task, repo_lfs_spec(entry))
            if source.is_file():
                with source.open("rb") as stream:
                    if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                        absent.append(entry)  # Unsmudged public LFS fails closed as missing.
                        continue
            if authentic(source, entry):
                continue
        if entry.get("kind") == "git-history":
            from payload_check import git_history_spec
            git_history_spec(entry)
            if outcome(verified[id(entry)]) or allow_fetch:
                continue
        elif (allow_fetch and entry["scope"] == "public" and entry.get("fetch")) or authentic(target, entry):
            continue
        absent.append(entry)
    return absent


def message(task, scope):
    return (f"PAYLOAD PENDING [{task.name}] ({scope}): "
            f"python3 tools/stage_payload.py --task {task.name} --from /path/to/payload")


def check(task, scope=None, allow_missing_references=False):
    absent = missing(task, scope)
    if allow_missing_references:
        absent = [entry for entry in absent if entry.get("retention") != "fetch-only"]
    if absent:
        raise ValueError(message(task, scope or "all") + "\n" +
                         "\n".join(entry["destination"] for entry in absent))


@contextmanager
def staging_transaction(task, entries):
    """Roll back every payload destination if any acquisition/authentication fails."""
    paths = [contained(task, row["destination"]) for row in entries
             if row.get("retention") != "fetch-only"]
    with tempfile.TemporaryDirectory(prefix=".payload-transaction-", dir=task) as temporary:
        before = []
        for index, path in enumerate(paths):
            backup = None
            if path.is_file():
                backup = Path(temporary) / str(index)
                # A rename publishes a different inode; retain the old bytes
                # and mode without copying a potentially large original body.
                os.link(path, backup)
            before.append((path, path.exists(), backup))
        try:
            yield
        except BaseException:
            for path, existed, backup in reversed(before):
                if backup is not None:
                    os.replace(backup, path)
                elif not existed:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                    parent = path.parent
                    while parent != Path(task):
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent = parent.parent
            raise


def stage(task, source=None, fetch=False, scope=None):
    task = Path(task).resolve()
    absent = missing(task, scope,
                     allow_unfetched_private=source is not None and not fetch)
    if not absent:
        return 0
    with staging_transaction(task, absent):
        return stage_entries(task, absent, source, fetch, scope)


def source_candidate(source, entry):
    names = [entry["destination"], entry.get("name", Path(entry["destination"]).name)]
    prefix = "environment/workload/"
    if entry["destination"].startswith(prefix):
        names.append(entry["destination"][len(prefix):])
    candidates = [contained(source, name) for name in names]
    predicate = Path.is_dir if entry.get("kind") == "git-history" else Path.is_file
    return next((path for path in candidates if predicate(path)), None)


def stage_entries(task, absent, source=None, fetch=False, scope=None):
    references = [entry for entry in absent if entry.get("retention") == "fetch-only"]
    if references and (fetch or source is None):
        raise ValueError("Fetch-only held-out inputs require verifier acquisition; "
                         "staging bodies into the worktree is forbidden")
    # --from restores retained bodies only. missing() has already validated
    # reference/lock agreement and rejected any reference body in the worktree.
    absent = [entry for entry in absent if entry.get("retention") != "fetch-only"]
    for entry in absent:
        if entry.get("kind") == "git-history":
            from payload_check import acquire_git_history
            destination = contained(task, entry["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
                prepared = Path(temporary) / "payload"
                candidate = source_candidate(source, entry) if source is not None else None
                if candidate is not None:
                    if not authentic(candidate, entry):
                        raise ValueError("Missing/invalid original Git history directory")
                    shutil.copytree(candidate, prepared, symlinks=True)
                elif source is not None:
                    raise ValueError("Missing/invalid original Git history directory")
                elif fetch:
                    acquire_git_history(entry, prepared)
                else:
                    raise ValueError(message(task, entry["scope"]))
                if not authentic(prepared, entry):
                    raise ValueError("Git history inventories differ")
                prepared.rename(destination)
            continue
        if not entry.get("sha256"):
            raise ValueError(message(task, entry["scope"]) +
                             "; original operator manifest/hash is required: " +
                             entry["destination"])
        destination = contained(task, entry["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            blob = Path(temporary) / "payload"
            candidate = source_candidate(source, entry) if source is not None else None
            if source is not None and candidate is None and not fetch:
                raise ValueError("Missing source for " + entry["destination"])
            if candidate is not None:
                if not authentic(candidate, entry):
                    raise ValueError("Original payload size/SHA-256 mismatch: " + entry["destination"])
                shutil.copyfile(candidate, blob)
            elif entry.get("kind") == "repo-lfs":
                from payload_check import repo_lfs_spec
                candidate = contained(task, repo_lfs_spec(entry))
                if not authentic(candidate, entry):
                    raise ValueError("repo-lfs size/SHA-256 mismatch: " + str(candidate))
                shutil.copyfile(candidate, blob)
            elif fetch and not entry.get("fetch") and (entry.get("url") or entry.get("transport", {}).get("url", "")).startswith("https://"):
                transport = entry.get("transport")
                url = transport["url"] if transport else entry["url"]
                downloaded = Path(temporary) / "download" if transport else blob
                from payload_check import Fetcher
                # Same bounded/retrying transport and byte authentication as the
                # standalone public guard; transport and final identities stay pinned.
                pinned = dict(transport or entry, url=url)
                shutil.copyfile(Fetcher(ROOT / ".cache/public-inputs").url(pinned), downloaded)
                if transport:
                    if transport["kind"] != "png-rgb24":
                        raise ValueError("Unsupported payload transport recipe")
                    if not authentic(downloaded, transport):
                        raise ValueError("Transport size/SHA-256 mismatch")
                    # Same protected conversion as the original CharLS acquisition.
                    # Both transport bytes and final RGB plane are authenticated.
                    subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-y",
                                    "-i", str(downloaded), "-threads", "1",
                                    "-pix_fmt", "rgb24", "-f", "rawvideo", str(blob)], check=True)
            elif fetch and entry.get("fetch"):
                from payload_check import Fetcher
                blob.write_bytes(Fetcher(ROOT / ".cache/public-inputs").render(entry["fetch"]))
            else:
                raise ValueError(message(task, entry["scope"]))
            if not authentic(blob, entry):
                raise ValueError("Payload size/SHA-256 mismatch: " + entry["destination"])
            blob.replace(destination)
    check(task, scope, allow_missing_references=source is not None and not fetch)
    return len(absent)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Only this task's payload is changed. Catalog/provenance refresh is "
               "a separate maintenance operation requiring the relevant inputs.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scope", choices=["public", "heldout"], default="public")
    args = parser.parse_args()
    task = contained(ROOT / "tasks", args.task)
    try:
        if args.check:
            check(task, args.scope)
        else:
            receipt = {"task": task.name, "staged": stage(task, args.source, args.fetch, args.scope),
                       "catalog_refreshed": False}
            if git_history_roots(task, json.loads((task / "payload.json").read_text())):
                from payload_check import git_tool_receipt
                receipt["git_tool"] = git_tool_receipt()
            print(json.dumps(receipt))
            # Staging authenticates only this task. Catalog generation is a
            # separate, explicit maintenance operation: it may require bodies
            # from other tasks and must not mutate unrelated metadata here.
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
