"""Verifier-only content locks and safe pinned-archive reconstruction."""
import hashlib
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request
import re
import functools
import inspect
from urllib.parse import urlsplit
from contextvars import ContextVar
import importlib.util
import multiprocessing
import os

_ACQUIRED = ContextVar("heldout_acquired", default=frozenset())
_TASK_SOURCES = ContextVar("heldout_task_sources", default={})


def canonical_task(task):
    """Resolve only authenticated live views; private tests remain in the view."""
    task = Path(task).resolve()
    source = _TASK_SOURCES.get().get(task)
    if source is None:
        return task
    for child in source.iterdir():
        if child.name == "tests":
            continue
        link = task / child.name
        if not link.is_symlink() or link.resolve() != child.resolve():
            raise ValueError("Acquired task protected path changed: " + child.name)
    return source


def _attempt(function, item):
    try:
        return None, function(item)
    except Exception as error:
        return error, None


def concurrent_outcomes(function, items):
    """Run independent per-item integrity work on a bounded process pool.

    Strict fsck and per-object inventory hashing are CPU-bound Python/Git work
    (GIL-bound under threads), so items run in forked workers, at most
    min(8, available cores). `function` must be module-level. Returns
    (error, value) pairs in input order; callers consume them with outcome()
    at the point their serial loop made the call, so the first reported
    failure, its message and fail-closed behavior are unchanged.
    """
    items = list(items)
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    workers = max(1, min(8, cores, len(items)))
    if workers == 1:
        return [_attempt(function, item) for item in items]
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as pool:
        return [future.result() for future in [pool.submit(_attempt, function, item) for item in items]]


def outcome(result):
    error, value = result
    if error is not None:
        raise error
    return value


def git_tools(tests=None):
    """Reuse the protected acquisition implementation, including verifier images."""
    path = Path(__file__).resolve().parents[1] / "tools/payload_check.py"
    if path.is_file():
        from tools import payload_check
        return payload_check
    # Verifier images ship the byte-identical protected guard with each task,
    # not the repository's host-only tools package.
    if tests is None:
        raise ValueError("Git reference validation requires protected task tools")
    path = Path(tests).parent / "environment/payload_check.py"
    spec = importlib.util.spec_from_file_location("_heldout_git_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reference_paths_overlap(names, locked_names):
    """Same strict Path ancestry policy, indexed by canonical path components."""
    names = list(names)
    if not names:
        return False
    all_names = names + list(locked_names)
    # Preserve legacy rejection/error ordering for malformed non-JSON keys.
    if any(not isinstance(name, str) for name in all_names):
        return any(other != name and (Path(name) in Path(other).parents
                                     or Path(other) in Path(name).parents)
                   for name in names for other in all_names)
    reference_paths = {Path(name) for name in names}
    all_paths = {Path(name) for name in all_names}
    for path in all_paths:
        is_reference = path in reference_paths
        if any(parent in reference_paths or (is_reference and parent in all_paths)
               for parent in path.parents):
            return True
    return False


def _under_reference(relative, reference_paths):
    if not reference_paths:
        return False
    path = Path(relative)
    return path in reference_paths or any(parent in reference_paths for parent in path.parents)


def references(lock, tests=None):
    """Validate non-redistributable input declarations, never expected outputs."""
    rows = lock.get("references", {})
    if not isinstance(rows, dict):
        raise ValueError("Invalid held-out references")
    for name, entry in rows.items():
        contained(Path("/heldout-reference-root"), name)
        parts = Path(name).parts
        kind = entry.get("kind", "file")
        if (Path(name).as_posix() != name or len(parts) < 3 or parts[:1] != ("heldout",)
                or (parts[1] not in ("files", "data", "projects")
                    and not (parts[1] == "histories" and kind == "git-history"))
                or name in lock["files"]):
            raise ValueError("Reference must be a separate held-out input")
        if (entry.get("scope") != "heldout" or entry.get("role") != "input"
                or entry.get("license_basis") != "fetch-only; no redistribution"):
            raise ValueError("Reference requires fetch-only input license basis")
        if kind == "git-history":
            git_tools(tests).git_history_spec({**entry, "scope": "public", "destination": name})
        elif kind == "file":
            parsed = urlsplit(entry.get("url", ""))
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.fragment
                    or parsed.port not in (None, 443)
                    or "\\" in entry["url"] or any(ord(c) <= 32 for c in entry["url"])):
                raise ValueError("Reference requires credential-free pinned HTTPS URL")
            if (type(entry.get("bytes")) is not int or entry["bytes"] < 0
                    or not re.fullmatch("[a-f0-9]{64}", str(entry.get("sha256", "")))):
                raise ValueError("Reference requires exact size and SHA-256")
        else:
            raise ValueError("Unknown held-out reference kind")
    if _reference_paths_overlap(rows, lock["files"]):
        raise ValueError("Overlapping held-out reference paths")
    return rows


def reference_matches(path, entry, tests=None):
    if entry.get("kind") == "git-history":
        return git_tools(tests).verified(path, {**entry, "scope": "public"})
    return (path.is_file() and not path.is_symlink()
            and path.stat().st_size == entry["bytes"] and sha(path) == entry["sha256"])


def _reference_row(row):
    path, entry, tests = row
    return reference_matches(path, entry, tests)


def _acquire_reference(row):
    task, view, name, entry = row
    destination = contained(view / "tests", name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if entry.get("kind") == "git-history":
        git_tools(task / "tests").acquire_git_history({**entry, "scope": "public"}, destination)
    else:
        with urllib.request.urlopen(entry["url"], timeout=180) as response, destination.open("xb") as stream:
            # Bound downloads to the pinned size, including one byte
            # to detect an oversized response without filling the disk.
            remaining = entry["bytes"] + 1
            while remaining:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                stream.write(chunk)
                remaining -= len(chunk)
    return reference_matches(destination, entry, view / "tests")


def reference_payload(rows, manifest):
    """Bind reference declarations to payload metadata without acquiring bodies."""
    records = [entry for entry in manifest["files"] if entry.get("retention") == "fetch-only"]
    declared = {entry["destination"]: entry for entry in records}
    if len(declared) != len(records) or set(declared) != {"tests/" + name for name in rows}:
        raise ValueError("Fetch-only payload/held-out lock declarations differ")
    for name, entry in rows.items():
        record = declared["tests/" + name]
        if {k: v for k, v in record.items() if k not in ("name", "destination", "retention")} != entry:
            raise ValueError("Fetch-only payload/held-out lock pins differ")
    return declared


@contextmanager
def materialized_task(task):
    """Verifier-only external acquisition; never stage licensed bodies in Git."""
    task = Path(task).resolve()
    if task in _ACQUIRED.get():
        check(task / "tests", require_available=True)
        yield task
        return
    lock = check(task / "tests", allow_missing_references=True)
    rows = references(lock, task / "tests")
    if not rows:
        yield task
        return
    # Even a byte-correct body is forbidden in the source worktree.
    for name in rows:
        path = contained(task / "tests", name)
        if path.exists() or path.is_symlink():
            raise ValueError("Fetch-only reference body retained in worktree: " + name)
    with tempfile.TemporaryDirectory(prefix="heldout-acquisition-", dir="/tmp") as temporary:
        view = Path(temporary) / task.name
        view.mkdir()
        for child in task.iterdir():
            if child.name == "tests":
                shutil.copytree(child, view / "tests", symlinks=True)
            else:
                (view / child.name).symlink_to(child, target_is_directory=child.is_dir())
        # Distinct, non-overlapping destinations (references()); report in lock order.
        ordered = [(task, view, name, entry) for name, entry in rows.items()]
        for (_, _, name, _), result in zip(ordered, concurrent_outcomes(_acquire_reference, ordered)):
            if not outcome(result):
                raise ValueError("Held-out reference size/SHA-256 mismatch: " + name)
        check(view / "tests", require_available=True)
        token = _ACQUIRED.set(_ACQUIRED.get() | {view})
        source_token = _TASK_SOURCES.set({**_TASK_SOURCES.get(), view: task})
        try:
            yield view
        finally:
            _TASK_SOURCES.reset(source_token)
            _ACQUIRED.reset(token)


def acquired(function):
    """Acquire before builds, gates or timing, with lifetime covering execution."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        if (bound.arguments.get("plumbing", False)
                or bound.arguments.get("public_only", False)
                or getattr(bound.arguments.get("args"), "gates_only", False)):
            return function(*args, **kwargs)
        task = Path(bound.arguments["task"])
        lock_path = task / "tests/heldout.lock.json"
        if not lock_path.is_file():
            # Preserve earlier task-admission errors (e.g. unqualified tasks);
            # the original verifier still requires its lock before execution.
            return function(*args, **kwargs)
        # Nested run/compare calls already use the authenticated external view.
        if not references(json.loads(lock_path.read_text()), task / "tests"):
            return function(*args, **kwargs)
        with materialized_task(task) as view:
            bound.arguments["task"] = view
            return function(*bound.args, **bound.kwargs)
    return wrapped


def reject_lfs_pointer(path):
    """Never authenticate an un-smudged pointer as verifier content."""
    path = Path(path)
    if path.is_file():
        with path.open("rb") as stream:
            if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError("held-out LFS object not fetched (git lfs pull): " + str(path))


def sha(path):
    reject_lfs_pointer(path)
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained(root, name):
    path = Path(name)
    result = (Path(root) / path).resolve()
    if path.is_absolute() or ".." in path.parts or not result.is_relative_to(Path(root).resolve()):
        raise ValueError("Held-out path escapes tests/")
    return result


def check(tests, require_available=False, allow_missing_references=False):
    tests = Path(tests)
    reject_lfs_pointer(tests / "heldout.lock.json")
    lock = json.loads((tests / "heldout.lock.json").read_text())
    if lock["schema_version"] != 1:
        raise ValueError("Unknown held-out lock version")
    if lock["status"] not in ("available", "unavailable"):
        raise ValueError("Unknown held-out lock status")
    rows = references(lock, tests)
    payload_path = tests.parent / "payload.json"
    if allow_missing_references and (rows or payload_path.is_file()):
        reference_payload(rows, json.loads(payload_path.read_text()))
    pending, failure = [], None
    for name, entry in rows.items():
        try:
            path = contained(tests, name)
            raw = tests / name
            if any(p.is_symlink() for p in [raw, *raw.parents] if p.is_relative_to(tests) and p != tests):
                raise ValueError("Symlink in held-out reference")
            if allow_missing_references:
                if path.exists():
                    raise ValueError("Fetch-only reference body retained in worktree: " + name)
                continue
        except Exception as error:
            failure = error  # Raised after every earlier row's result, as before.
            break
        pending.append((name, (path, entry, tests)))
    results = concurrent_outcomes(_reference_row, [row for _, row in pending])
    for (name, _), result in zip(pending, results):
        if not outcome(result):
            raise ValueError("Held-out reference unavailable or mismatched: " + name)
    if failure is not None:
        raise failure
    for name, expected in lock["files"].items():
        path = contained(tests, name)
        raw = tests / name
        symlinked = any(p.is_symlink() for p in [raw, *raw.parents] if p != tests and p.is_relative_to(tests))
        if not symlinked and not path.exists():
            # A public export omits bodies it may not redistribute; they are
            # recovered locally before scoring (see the Task README).
            raise ValueError("Held-out file not present: " + name +
                             "; recover it as the Task README describes before scoring")
        if symlinked or not path.is_file() or sha(path) != expected:
            raise ValueError("Held-out hash mismatch: " + name)
    actual = set()
    reference_paths = {Path(name) for name in rows}
    for path in (tests / "heldout").rglob("*"):
        if path.is_file():
            relative = path.relative_to(tests).as_posix()
            if _under_reference(relative, reference_paths):
                continue
            reject_lfs_pointer(path)
            actual.add(path.relative_to(tests).as_posix())
    if actual != {name for name in lock["files"] if name.startswith("heldout/")}:
        raise ValueError("Uncommitted or missing held-out file")
    manifest = json.loads((tests / "heldout/manifest.json").read_text())
    if require_available and (lock["status"] != "available" or not manifest["splits"]):
        task = tests.parent.name
        raise ValueError(f"PAYLOAD PENDING [{task}] (heldout): "
                         f"python3 tools/stage_payload.py --task {task} --scope heldout "
                         "--from /path/to/payload; held-out payload not present, "
                         "no scored run allowed")
    # For an available lock, both case contents and expected-result metadata must be committed.
    if lock["status"] == "available":
        if "heldout/manifest.json" not in lock["files"]:
            raise ValueError("Uncommitted held-out manifest")
        count = 0
        for batches in manifest["splits"].values():
            for batch in batches:
                for doc in batch["docs"]:
                    count += 1
                    name = "heldout/files/" + doc["sha256"]
                    if (lock["files"].get(name) != doc["sha256"]
                            and rows.get(name, {}).get("sha256") != doc["sha256"]):
                        raise ValueError("Case is not content-locked")
                    if not doc.get("expect_sha") or not isinstance(doc.get("expect_len"), int):
                        raise ValueError("Missing expected result")
        if not count:
            raise ValueError("Empty held-out set")
    return lock


def project_root(tests, fallback):
    """Select authenticated private projects, never silently public substitutes."""
    tests = Path(tests)
    private = tests / "heldout/projects"
    if private.exists():
        check(tests, require_available=True)
        if not private.is_dir() or private.is_symlink():
            raise ValueError("Invalid held-out projects root")
        return private
    return Path(fallback)


@contextmanager
def workload_view(tests, fallback, enabled=True):
    """Overlay locked private metadata for host preparation, never baseline code.

    evaluate() has already authenticated the full held-out lock. Recheck just
    the small overlay here, rather than rehashing GB of arrays for every case.
    Worker programs still resolve to the unchanged pristine image workload.
    """
    tests, fallback = Path(tests), Path(fallback)
    overlay = tests / "heldout/workload"
    if not enabled or not overlay.exists():
        yield fallback
        return
    lock = json.loads((tests / "heldout.lock.json").read_text())
    if lock["status"] != "available":
        raise ValueError("Private workload metadata is unavailable")
    if overlay.is_symlink():
        raise ValueError("Symlink in private workload metadata")
    for path in overlay.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in private workload metadata")
        if not path.is_file():
            continue
        name = path.relative_to(tests).as_posix()
        if path.suffix not in (".json", ".txt"):
            raise ValueError("Private workload overlay may contain only JSON/text metadata")
        if lock["files"].get(name) != sha(path):
            raise ValueError("Uncommitted private workload metadata: " + name)
    with tempfile.TemporaryDirectory(prefix="heldout-workload-") as temporary:
        root = Path(temporary)
        shutil.copytree(fallback, root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("inputs", "__pycache__"))
        shutil.copytree(overlay, root, dirs_exist_ok=True)
        yield root


def fetch_archives(tests, destination):
    """Build-time only. Every archive needs a public pinned URL and SHA-256."""
    tests = Path(tests)
    lock = check(tests, allow_missing_references=tests.parent.resolve() not in _ACQUIRED.get())
    if lock["status"] == "unavailable":
        print("heldout unavailable: " + lock.get("reason", "No reason provided"))
        return
    archives = lock["archives"]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for archive in archives:
        url = archive["url"]
        if not url.startswith("https://") or "@" in url.split("/")[2]:
            raise ValueError("Expected public HTTPS archive")
        target = contained(destination, archive["project"])
        if target.exists():
            raise ValueError("Refusing to overwrite held-out project")
        with tempfile.TemporaryDirectory() as temporary:
            blob = Path(temporary) / "archive.tar"
            with urllib.request.urlopen(url, timeout=180) as response, blob.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if sha(blob) != archive["sha256"]:
                raise ValueError("Archive checksum mismatch")
            unpack = Path(temporary) / "unpack"
            unpack.mkdir()
            with tarfile.open(blob) as stream:
                stream.extractall(unpack, filter="data")
            roots = list(unpack.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise ValueError("Expected one archive root")
            # Runner verifies the committed per-case inventory_sha256 before use.
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(roots[0], target)


if __name__ == "__main__":
    import sys
    fetch_archives(Path(sys.argv[1]), Path(sys.argv[2]))
