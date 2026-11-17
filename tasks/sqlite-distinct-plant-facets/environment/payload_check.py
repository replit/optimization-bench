#!/usr/bin/env python3
"""Standalone public-only Docker build guard; identical in every environment."""
import hashlib
import json
from pathlib import Path
import sys
import base64
import io
import os
import shutil
import tarfile
import tempfile
import urllib.request
import threading
import struct
import zlib
import concurrent.futures
import zipfile
import subprocess
import re
import stat
import ipaddress
from functools import lru_cache
from urllib.parse import urlsplit


def payload_target(root, name):
    root = Path(root).resolve()
    relative = Path(name)
    target = root / relative
    if not name or relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe payload destination")
    for component in [target, *target.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("Symlink in payload destination")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Payload destination escapes root")
    return target


def repo_lfs_spec(record):
    """Public recovery-only repository transport, staged before image builds."""
    if record.get("kind") != "repo-lfs" or record.get("scope", "public") != "public":
        raise ValueError("repo-lfs is public-only")
    source = record.get("source", "")
    if not isinstance(source, str) or not source.startswith("public/files/"):
        raise ValueError("repo-lfs source must be under public/files/, never environment/")
    if source == "public/files/":
        raise ValueError("repo-lfs source must name a file")
    payload_target(Path("/"), source)
    if not record.get("reason") or not isinstance(record["reason"], str) or not record["reason"].strip():
        raise ValueError("repo-lfs requires a provenance reason")
    if any(record.get(key) for key in ("url", "fetch", "transport", "repository")):
        raise ValueError("repo-lfs forbidden with an upstream URL or recipe")
    if (type(record.get("bytes")) is not int or record["bytes"] < 0 or
            not re.fullmatch("[a-f0-9]{64}", str(record.get("sha256", "")))):
        raise ValueError("repo-lfs requires exact size and SHA-256")
    return source


def reject_lfs_pointer(path):
    if path.is_file():
        with path.open("rb") as stream:
            if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                raise ValueError("public LFS object not fetched (git lfs pull): " + str(path))


def git_history_spec(record):
    """Directory integrity is a canonical object stream, not publisher pack bytes."""
    if record.get("kind") != "git-history":
        raise ValueError("Expected git-history directory payload")
    url, commit = record.get("repository"), record.get("commit")
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise ValueError("Expected a public HTTPS Git repository")
    parsed = urlsplit(url)
    if (not url.startswith("https://") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or "\\" in url or "%" in parsed.netloc or parsed.port not in (None, 443)):
        raise ValueError("Expected credential-free public HTTPS Git repository")
    host = parsed.hostname.lower()
    if "." not in host or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Git repository must be public")
    if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", part) for part in host.split(".")):
        raise ValueError("Git repository must use a public DNS hostname, not numeric aliases")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Git repository must be public")
    except ValueError as error:
        if str(error) == "Git repository must be public":
            raise
    if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise ValueError("Git history requires a full commit ID")
    if record.get("ref_policy") != "one-detached-commit-closure":
        raise ValueError("Unsupported Git history ref policy")
    inventory = record.get("object_inventory", {})
    if inventory.get("algorithm") != "sha256-canonical-git-objects-v1":
        raise ValueError("Unsupported Git object inventory")
    for name in ("object_count", "bytes"):
        if type(inventory.get(name)) is not int or inventory[name] <= 0:
            raise ValueError("Git inventory counts/bytes must be pinned positive integers")
    for digest in (inventory.get("sha256"), record.get("checkout_inventory_sha256")):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Git history inventories require SHA-256 commitments")
    warnings = record.get("fsck_warnings", {"count": 0, "sha256": hashlib.sha256(b"").hexdigest()})
    if (not isinstance(warnings, dict) or set(warnings) != {"count", "sha256"}
            or type(warnings["count"]) is not int or warnings["count"] < 0
            or not isinstance(warnings["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", warnings["sha256"])):
        raise ValueError("Git fsck warnings require an exact count and sorted-line SHA-256")
    gitlinks = record.get("gitlinks", {})
    if not isinstance(gitlinks, dict):
        raise ValueError("Gitlinks must be a pinned path-to-object-ID mapping")
    for path, oid in gitlinks.items():
        if (not isinstance(path, str) or not path or Path(path).is_absolute()
                or any(part in ("", ".", "..", ".git") for part in path.split("/"))
                or not isinstance(oid, str) or not re.fullmatch("[0-9a-f]{" + str(len(commit)) + "}", oid)):
            raise ValueError("Invalid pinned gitlink")
    if record.get("scope", "public") != "public":
        raise ValueError("Git history acquisition is public-only")
    if "destination" in record:
        path = Path(record["destination"])
        if (path.is_absolute() or path == Path(".") or ".." in path.parts
                or any(part.lower() == ".git" for part in path.parts)):
            raise ValueError("Unsafe Git history destination")
    return commit


def _git_config(commit, bare=False):
    version = 1 if len(commit) == 64 else 0
    text = (f"[core]\n\trepositoryformatversion = {version}\n\tfilemode = true\n"
            f"\tbare = {'true' if bare else 'false'}\n\tlogallrefupdates = false\n\tautocrlf = false\n")
    if version:
        text += "[extensions]\n\tobjectformat = sha256\n"
    return text.encode()


@lru_cache(maxsize=1)
def _resolve_git():
    """Resolve the trusted caller tool once; never restore its environment."""
    override = os.environ.get("OPTIMBENCH_GIT")
    if override and not Path(override).is_absolute():
        raise ValueError("OPTIMBENCH_GIT must be an absolute executable path")
    found = override or shutil.which("git")
    if not found:
        raise ValueError("Git executable unavailable; install Git or set OPTIMBENCH_GIT")
    path = Path(found).resolve()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("Git executable must be an absolute executable file")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        version = subprocess.run([str(path), "--version"], capture_output=True, env=_git_env(),
                                 timeout=30, check=True).stdout.decode().strip()
    except subprocess.SubprocessError as error:
        raise ValueError("Unable to validate Git executable version") from error
    if not re.fullmatch(r"git version [^\r\n]+", version):
        raise ValueError("Unrecognized Git executable version")
    return str(path), version, digest


def git_tool_receipt():
    """Execution provenance, deliberately separate from input integrity pins."""
    return dict(zip(("path", "version", "sha256"), _resolve_git()))


def _git_binary():
    return _resolve_git()[0]


def _git_env():
    # No inherited Git configuration, helpers, credentials, protocol rewrites,
    # alternates, index, object-directory, namespace, graft or replacement state.
    return {"PATH": os.defpath, "HOME": "/dev/null",
            "XDG_CONFIG_HOME": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_COUNT": "0",
            "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "https", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}


def _git_args(gitdir, *args):
    return [_git_binary(), "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
            "-c", "safe.directory=" + str(gitdir),
            "-c", "safe.directory=" + str(Path(gitdir).parent),
            "-c", "core.attributesFile=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "credential.helper=", "-c", "protocol.allow=never",
            "-c", "credential.interactive=false", "-c", "http.authMethod=basic",
            "-c", "http.emptyAuth=false", "-c", "http.proactiveAuth=none",
            "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
            "-c", "http.sslVerify=true", "-c", "pack.writeReverseIndex=false",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0",
            "-c", "fetch.writeCommitGraph=false", "-c", "pack.threads=1",
            "-c", "submodule.recurse=false", "-c", "pack.allowPackReuse=false",
            "--git-dir=" + str(gitdir), *args]


def _git(gitdir, *args, input=None):
    result = subprocess.run(_git_args(gitdir, *args), input=input, capture_output=True,
                            env=_git_env(), timeout=1800, umask=0o022)
    if result.returncode:
        raise ValueError("Git history command failed: " + " ".join(args[:2]) +
                         ": " + result.stderr.decode(errors="replace")[:1000])
    return result.stdout


def _git_init(gitdir, commit, bare):
    gitdir.mkdir()
    (gitdir / "objects/pack").mkdir(parents=True)
    (gitdir / "objects/info").mkdir()
    (gitdir / "refs").mkdir()
    (gitdir / "config").write_bytes(_git_config(commit, bare))
    (gitdir / "HEAD").write_bytes(b"ref: refs/heads/payload-unborn\n" if bare else commit.encode() + b"\n")


def _git_audit(gitdir, commit, transfer=False):
    if gitdir.is_symlink() or not gitdir.is_dir():
        raise ValueError("Expected an isolated .git directory")
    directories = {"objects", "objects/pack", "objects/info", "refs", "refs/heads", "refs/tags"}
    for path in gitdir.rglob("*"):
        relative = path.relative_to(gitdir).as_posix()
        if path.is_symlink():
            raise ValueError("Symlink in Git semantic metadata")
        if path.is_dir():
            if relative not in directories and not re.fullmatch("objects/[0-9a-f]{2}", relative):
                raise ValueError("Extra Git semantic directory: " + relative)
            continue
        allowed = relative in {"HEAD", "config"} or relative == "index" and not transfer
        allowed |= bool(re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{" + str(len(commit) - 2) + "}", relative))
        allowed |= bool(re.fullmatch(r"objects/pack/pack-[0-9a-f]{" + str(len(commit)) + r"}\.(?:pack|idx)", relative))
        if not allowed or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("Extra/shared Git semantic state: " + relative)
    expected_head = b"ref: refs/heads/payload-unborn\n" if transfer else commit.encode() + b"\n"
    if ((gitdir / "config").read_bytes() != _git_config(commit, transfer)
            or (gitdir / "HEAD").read_bytes() != expected_head):
        raise ValueError("Unexpected Git configuration, refs or HEAD")


def _git_fetch(gitdir, url, commit):
    _git(gitdir, "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
         "--refmap=", url, commit)


def _git_fsck(gitdir, expected=None, transfer=False):
    run = subprocess.run(_git_args(gitdir, "fsck", "--full", "--strict",
                                   "--no-reflogs", "--no-progress"),
                         capture_output=True, env=_git_env(), timeout=1800)
    if run.returncode:
        raise ValueError("Strict Git fsck failed: " + run.stderr.decode(errors="replace")[:1000])
    warnings = []
    for line in (run.stdout + run.stderr).splitlines():
        if line.startswith((b"warning ", b"warning:")):
            warnings.append(line)
        elif line.startswith(b"dangling ") and not transfer:
            raise ValueError("Git object store contains extra or missing closure objects")
        elif line and not (transfer and line.startswith((b"notice: ", b"dangling "))):
            raise ValueError("Unexpected strict Git fsck output")
    canonical = b"".join(line + b"\n" for line in sorted(warnings))
    actual = {"count": len(warnings), "sha256": hashlib.sha256(canonical).hexdigest()}
    if expected is not None and actual != expected:
        raise ValueError("Strict Git fsck warning count/SHA-256 differs from pinned list")
    return actual


def _git_tree(gitdir, commit):
    rows = []
    raw = _git(gitdir, "ls-tree", "-r", "-z", "--full-tree", commit)
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, kind, oid = header.split(b" ")
        parts = path.split(b"/")
        if (not (mode in (b"100644", b"100755", b"120000") and kind == b"blob"
                 or mode == b"160000" and kind == b"commit")
                or any(p in (b"", b".", b"..") or p.lower() == b".git" for p in parts)):
            raise ValueError("Unsupported/unsafe Git checkout entry")
        rows.append((path, mode, oid))
    return sorted(rows)


def _git_index(rows, commit):
    data = bytearray(b"DIRC" + struct.pack(">II", 2, len(rows)))
    for path, mode, oid in rows:
        entry = (struct.pack(">10I", 0, 0, 0, 0, 0, 0, int(mode, 8), 0, 0, 0)
                 + bytes.fromhex(oid.decode()) + struct.pack(">H", min(len(path), 4095)) + path + b"\0")
        data.extend(entry + b"\0" * (-len(entry) % 8))
    digest = hashlib.sha256 if len(commit) == 64 else hashlib.sha1
    return bytes(data) + digest(data).digest()


def _git_object_inventory(gitdir, commit, expected=None):
    ids = sorted(set(_git(gitdir, "rev-list", "--objects", "--no-object-names", commit).splitlines()))
    all_ids = set(_git(gitdir, "cat-file", "--batch-all-objects",
                       "--batch-check=%(objectname)").splitlines())
    if all_ids != set(ids):
        raise ValueError("Git object store contains extra or missing closure objects")
    if expected and len(ids) != expected["object_count"]:
        raise ValueError("Git closure object count mismatch")
    stream = subprocess.Popen(_git_args(gitdir, "cat-file", "--batch"), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_git_env())
    digest, framed_bytes = hashlib.sha256(), 0
    try:
        for oid in ids:
            if not re.fullmatch(b"[0-9a-f]{" + str(len(commit)).encode() + b"}", oid):
                raise ValueError("Invalid Git object ID")
            stream.stdin.write(oid + b"\n")
            stream.stdin.flush()
            header = stream.stdout.readline()
            fields = header.rstrip(b"\n").split(b" ")
            if len(fields) != 3 or fields[0] != oid or fields[1] not in (b"commit", b"tree", b"blob", b"tag"):
                raise ValueError("Missing/ambiguous Git object")
            kind, length = fields[1], int(fields[2])
            if length < 0:
                raise ValueError("Invalid Git object length")
            for value in (oid, kind):
                digest.update(struct.pack(">Q", len(value)) + value)
                framed_bytes += 8 + len(value)
            digest.update(struct.pack(">Q", length))
            framed_bytes += 8 + length
            if expected and framed_bytes > expected["bytes"]:
                raise ValueError("Git canonical object byte budget exceeded")
            object_hash = (hashlib.sha256() if len(commit) == 64 else hashlib.sha1())
            object_hash.update(kind + b" " + str(length).encode() + b"\0")
            remaining = length
            while remaining:
                chunk = stream.stdout.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("Truncated Git object")
                remaining -= len(chunk)
                digest.update(chunk)
                object_hash.update(chunk)
            if stream.stdout.read(1) != b"\n" or object_hash.hexdigest().encode() != oid:
                raise ValueError("Raw Git object ID mismatch")
        stream.stdin.close()
        if stream.wait(timeout=1800):
            raise ValueError("Git cat-file failed")
    finally:
        if stream.poll() is None:
            stream.kill()
            stream.wait()
        for handle in (stream.stdin, stream.stdout, stream.stderr):
            if handle is not None:
                handle.close()
    return {"algorithm": "sha256-canonical-git-objects-v1", "object_count": len(ids),
            "bytes": framed_bytes, "sha256": digest.hexdigest()}


def _checkout_inventory(root, expected_rows, commit):
    rows = {}
    expected = {path: (mode.decode(), oid.decode()) for path, mode, oid in expected_rows}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        key = relative.as_posix()
        declaration = expected.get(os.fsencode(key))
        if declaration and declaration[0] == "160000":
            if (path.is_symlink() or not path.is_dir() or any(path.iterdir())
                    or stat.S_IMODE(path.stat().st_mode) != 0o755):
                raise ValueError("Gitlink must remain an empty uninitialized directory")
            rows[key] = {"mode": "160000", "gitlink": declaration[1], "initialized": False}
            continue
        if path.is_symlink():
            target = os.readlink(path)
            try:
                safe = path.resolve().is_relative_to(root.resolve())
            except (RuntimeError, OSError):
                safe = False
            if os.path.isabs(target) or not safe or ".git" in Path(target).parts:
                raise ValueError("Unsafe checkout symlink")
            raw, mode = os.fsencode(target), "120000"
        elif path.is_dir():
            if stat.S_IMODE(path.stat().st_mode) != 0o755:
                raise ValueError("Checkout directory mode mismatch")
            continue
        elif path.is_file() and path.stat().st_nlink == 1:
            permissions = stat.S_IMODE(path.stat().st_mode)
            if permissions not in (0o644, 0o755):
                raise ValueError("Checkout file mode mismatch")
            raw, mode = path.read_bytes(), "100755" if permissions == 0o755 else "100644"
        else:
            raise ValueError("Unsupported/shared checkout entry")
        if not declaration or declaration[0] != mode:
            raise ValueError("Checkout differs from original Git tree modes")
        rows[key] = {"mode": mode, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _checkout_original(gitdir, rows, destination):
    """Git's own built-in checkout transforms; no hooks/configured filter drivers."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    _git(gitdir, "--work-tree=" + str(destination), "checkout-index", "--all", "--force",
         "--prefix=" + str(destination) + "/")
    for path, mode, _ in rows:
        if mode == b"160000":
            (destination / os.fsdecode(path)).mkdir(parents=True, exist_ok=True)
    for path in destination.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)
    destination.chmod(0o755)


def git_history_snapshot(root, commit, expected=None, fsck_warnings=None):
    """Authenticate canonical repository state and return transport-independent inventories."""
    root = Path(root)
    gitdir = root / ".git"
    _git_audit(gitdir, commit)
    if _git(gitdir, "cat-file", "-t", commit) != b"commit\n":
        raise ValueError("Pinned Git object is not a commit")
    warnings = _git_fsck(gitdir, fsck_warnings)
    rows = _git_tree(gitdir, commit)
    if (gitdir / "index").read_bytes() != _git_index(rows, commit):
        raise ValueError("Changed/noncanonical Git index")
    expected_files = {path for path, mode, _ in rows if mode != b"160000"}
    actual_files, actual_dirs = set(), set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        (actual_files if path.is_symlink() or not path.is_dir() else actual_dirs).add(os.fsencode(relative.as_posix()))
    expected_dirs = {b"/".join(path.split(b"/")[:i]) for path in expected_files
                     for i in range(1, len(path.split(b"/")))}
    for path, mode, _ in rows:
        if mode == b"160000":
            expected_dirs.update(b"/".join(path.split(b"/")[:i]) for i in range(1, len(path.split(b"/")) + 1))
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise ValueError("Extra/missing checkout files or directories")
    actual_checkout = _checkout_inventory(root, rows, commit)
    # A forged checkout pin or edited .gitattributes must not bless bytes that
    # differ from an independent checkout of the original, canonical index.
    with tempfile.TemporaryDirectory(prefix="git-original-checkout-") as temporary:
        _checkout_original(gitdir, rows, Path(temporary))
        original_checkout = _checkout_inventory(Path(temporary), rows, commit)
    if actual_checkout != original_checkout:
        raise ValueError("Checkout differs from Git's original attribute/checkout semantics")
    return {"object_inventory": _git_object_inventory(gitdir, commit, expected),
            "checkout_inventory_sha256": actual_checkout, "fsck_warnings": warnings,
            "gitlinks": {os.fsdecode(path): oid.decode() for path, mode, oid in rows if mode == b"160000"}}


def materialize_git_history(repository, commit, destination, expected=None, fsck_warnings=None, receipt=None):
    """Authoring primitive; guard callers MUST compare the returned inventories to pins."""
    validation = {"kind": "git-history", "repository": repository, "commit": commit,
                  "ref_policy": "one-detached-commit-closure",
                  "object_inventory": {"algorithm": "sha256-canonical-git-objects-v1",
                                       "object_count": 1, "bytes": 1, "sha256": "0" * 64},
                  "checkout_inventory_sha256": "0" * 64}
    git_history_spec(validation)
    if receipt is not None:
        receipt["git_tool"] = git_tool_receipt()
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Git history destination must be fresh")
    destination.mkdir(mode=0o755)
    with tempfile.TemporaryDirectory(prefix="git-history-transfer-") as temporary:
        transfer = Path(temporary) / "repo"
        _git_init(transfer, commit, True)
        _git_fetch(transfer, repository, commit)
        _git_audit(transfer, commit, transfer=True)
        if _git(transfer, "cat-file", "-t", commit) != b"commit\n":
            raise ValueError("Git history pin must resolve to a commit")
        _git_fsck(transfer, transfer=True)
        gitdir = destination / ".git"
        _git_init(gitdir, commit, False)
        _git(transfer, "pack-objects", "--revs", "--threads=1",
             str(gitdir / "objects/pack/pack"), input=commit.encode() + b"\n")
        rows = _git_tree(gitdir, commit)
        leaves = {path for path, _, _ in rows}
        for path, _, _ in rows:
            if any(b"/".join(path.split(b"/")[:i]) in leaves for i in range(1, len(path.split(b"/")))):
                raise ValueError("Conflicting checkout paths")
        (gitdir / "index").write_bytes(_git_index(rows, commit))
        _checkout_original(gitdir, rows, destination)
        for directory in destination.rglob("*"):
            if not directory.is_symlink() and directory.is_dir():
                directory.chmod(0o755)
        destination.chmod(0o755)
        return git_history_snapshot(destination, commit, expected, fsck_warnings)


def acquire_git_history(record, destination):
    commit = git_history_spec(record)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".git-history-") as temporary:
        prepared = Path(temporary) / "payload"
        wanted = {**record, "gitlinks": record.get("gitlinks", {}), "fsck_warnings": record.get("fsck_warnings", {
            "count": 0, "sha256": hashlib.sha256(b"").hexdigest()})}
        actual = materialize_git_history(record["repository"], commit, prepared,
                                         record["object_inventory"], wanted["fsck_warnings"])
        if any(actual[key] != wanted[key] for key in actual):
            raise ValueError("Git history canonical inventories differ from pinned commitments")
        if destination.exists() or destination.is_symlink():
            raise ValueError("Git history destination already exists")
        prepared.rename(destination)


def png_chunks(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected PNG signature")
    chunks, offset = [], 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        raw = data[offset:offset + size + 12]
        if len(raw) != size + 12 or zlib.crc32(raw[4:-4]) != struct.unpack(">I", raw[-4:])[0]:
            raise ValueError("PNG chunk integrity mismatch")
        chunks.append(raw)
        offset += size + 12
    return chunks


def png_time_chunk(raw):
    return (raw[4:8] == b"tIME" or
            raw[4:8] == b"tEXt" and raw[8:-4].split(b"\0", 1)[0] in
            (b"date:create", b"date:modify", b"date:timestamp"))


def png_stable_sha(data):
    return hashlib.sha256(b"\x89PNG\r\n\x1a\n" +
                          b"".join(raw for raw in png_chunks(data) if not png_time_chunk(raw))).hexdigest()


def verified_file(path, record):
    """Authenticate bytes independently of their acquisition recipe kind."""
    if not path.is_file() or path.stat().st_size != record["bytes"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]


def verified(path, record):
    if record.get("kind", "file") not in ("file", "git-history", "repo-lfs"):
        raise ValueError("Unknown payload kind")
    if record.get("kind") == "repo-lfs":
        repo_lfs_spec(record)
        reject_lfs_pointer(path)
    if record.get("kind") == "git-history":
        commit = git_history_spec(record)
        if not path.exists() and not path.is_symlink():
            return False
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Expected a Git history directory")
        wanted = {**record, "gitlinks": record.get("gitlinks", {}), "fsck_warnings": record.get("fsck_warnings", {
            "count": 0, "sha256": hashlib.sha256(b"").hexdigest()})}
        actual = git_history_snapshot(path, commit, record["object_inventory"], wanted["fsck_warnings"])
        if any(actual[key] != wanted[key] for key in actual):
            raise ValueError("Git history inventory mismatch")
        return True
    return verified_file(path, record)


class Fetcher:
    """Build-time public acquisition only; every transport and final result is pinned."""
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.locks = {}
        self.lock_guard = threading.Lock()
        self.checked = {}

    def url(self, record):
        with self.lock_guard:
            lock = self.locks.setdefault(record["url"], threading.Lock())
        with lock:
            return self._url(record)

    def _url(self, record):
        url = record["url"]
        if not url.startswith("https://") or "@" in url.split("/")[2]:
            raise ValueError("Expected a credential-free public HTTPS source")
        target = self.cache / hashlib.sha256(url.encode()).hexdigest()
        identity = (record["bytes"], record["sha256"])
        if target.is_file():
            stat = target.stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if self.checked.get(url) == (identity, stamp):
                return target
        if verified_file(target, record):
            stat = target.stat()
            self.checked[url] = (identity, (stat.st_ino, stat.st_size, stat.st_mtime_ns))
            return target
        target.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "optimbench-public-inputs/1"})
            with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as out:
                remaining = record["bytes"] + 1
                while remaining:
                    chunk = response.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
        except Exception as error:
            target.unlink(missing_ok=True)
            raise ValueError("Pinned public source unavailable: " + url + "; " + str(error)) from error
        if not verified_file(target, record):
            target.unlink(missing_ok=True)
            raise ValueError("Pinned public source size/SHA-256 differs: " + url)
        stat = target.stat()
        self.checked[url] = (identity, (stat.st_ino, stat.st_size, stat.st_mtime_ns))
        return target

    def render(self, recipe):
        kind = recipe["kind"]
        if kind == "url":
            return self.url(recipe).read_bytes()
        if kind == "literal":
            return base64.b64decode(recipe["base64"], validate=True)
        if kind == "zeros":
            return bytes(recipe["bytes"])
        if kind == "concat":
            return b"".join(self.render(part) for part in recipe["parts"])
        if kind == "tar-member":
            archive = self.url(recipe["archive"])
            member = recipe["member"]
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("Unsafe archive member")
            with tarfile.open(archive, "r:*") as stream:
                handle = stream.extractfile(member)
                if handle is None:
                    raise ValueError("Missing pinned archive member: " + member)
                data = handle.read()
            if (len(data) != recipe["bytes"] or
                    hashlib.sha256(data).hexdigest() != recipe["sha256"]):
                raise ValueError("Pinned archive member differs: " + member)
            return data
        if kind == "zip-member":
            archive = self.url(recipe["archive"])
            member = recipe["member"]
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("Unsafe ZIP member")
            with zipfile.ZipFile(archive) as stream:
                data = stream.read(member)
            if len(data) != recipe["bytes"] or hashlib.sha256(data).hexdigest() != recipe["sha256"]:
                raise ValueError("Pinned ZIP member differs")
            return data
        if kind == "ar-member":
            raw = self.url(recipe["archive"]).read_bytes()
            if not raw.startswith(b"!<arch>\n"):
                raise ValueError("Expected Debian ar archive")
            offset = 8
            while offset + 60 <= len(raw):
                header = raw[offset:offset + 60]
                size = int(header[48:58])
                name = header[:16].decode().strip().rstrip("/")
                data = raw[offset + 60:offset + 60 + size]
                if name == recipe["member"]:
                    if len(data) != recipe["bytes"] or hashlib.sha256(data).hexdigest() != recipe["sha256"]:
                        raise ValueError("Pinned Debian member differs")
                    return data
                offset += 60 + size + size % 2
            raise ValueError("Missing Debian data member")
        if kind == "png-rgb24":
            archive = self.url(recipe["archive"])
            output = self.cache / ("rgb24-" + recipe["sha256"])
            if not verified_file(output, recipe):
                subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-y",
                                "-i", str(archive), "-threads", "1", "-pix_fmt", "rgb24",
                                "-f", "rawvideo", str(output)], check=True)
                if not verified_file(output, recipe):
                    raise ValueError("Pinned RGB24 plane differs")
            return output.read_bytes()
        if kind == "png-recorded-timestamps":
            source = recipe["source"]
            url = source["url"]
            if not url.startswith("https://") or "@" in url.split("/")[2]:
                raise ValueError("Expected public PNG HTTPS source")
            target = self.cache / ("png-" + hashlib.sha256(url.encode()).hexdigest())
            raw = target.read_bytes() if target.is_file() else b""
            if len(raw) != source["bytes"] or png_stable_sha(raw) != source["stable_sha256"]:
                errors = []
                for candidate in [url, *source.get("alternate_urls", [])]:
                    try:
                        if not candidate.startswith("https://") or "@" in candidate.split("/")[2]:
                            raise ValueError("Expected public PNG HTTPS source")
                        request = urllib.request.Request(candidate, headers={"User-Agent": "optimbench-public-inputs/1"})
                        with urllib.request.urlopen(request, timeout=180) as response:
                            raw = response.read(source["bytes"] + 1)
                        if len(raw) != source["bytes"] or png_stable_sha(raw) != source["stable_sha256"]:
                            raise ValueError("PNG non-timestamp bytes differ")
                        break
                    except Exception as error:
                        errors.append(str(error))
                else:
                    raise ValueError("Pinned PNG source unavailable or differs: " + url + "; " + "; ".join(errors))
                target.write_bytes(raw)
            chunks = png_chunks(raw)
            actual = {index for index, chunk in enumerate(chunks) if png_time_chunk(chunk)}
            recorded = {row["index"] for row in recipe["recorded"]}
            if actual != recorded:
                raise ValueError("PNG timestamp layout differs")
            for row in recipe["recorded"]:
                chunk = base64.b64decode(row["base64"], validate=True)
                if not png_time_chunk(chunk) or len(png_chunks(b"\x89PNG\r\n\x1a\n" + chunk)) != 1:
                    raise ValueError("Only recorded PNG timestamp chunks may be restored")
                chunks[row["index"]] = chunk
            return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)
        raise ValueError("Unsupported public acquisition recipe: " + str(kind))


def public_manifest(root):
    """The standalone build guard never reads the task's private manifest."""
    return json.loads((Path(root) / "payload-public.json").read_text())


def acquire(root, cache=None):
    root = Path(root).resolve()
    manifest = public_manifest(root)
    for record in manifest["files"]:
        if record.get("scope", "public") != "public":
            raise ValueError("Public acquisition rejects private payloads")
        payload_target(root, record["destination"])
    unresolved = [record["destination"] for record in manifest["files"]
                  if not record.get("fetch") and record.get("kind") != "git-history"
                  and not verified(payload_target(root, record["destination"]), record)]
    if unresolved:
        raise ValueError(f"PAYLOAD PENDING [{manifest['task']}] (public): "
                         f"python3 tools/stage_payload.py --task {manifest['task']} "
                         "--from /path/to/payload\n" + "\n".join(unresolved))
    with tempfile.TemporaryDirectory(prefix="public-input-fetch-") as temporary:
        fetcher = Fetcher(cache or temporary)
        sources = {}
        def enumerate_sources(value):
            if isinstance(value, dict):
                if value.get("url") and "sha256" in value and "bytes" in value:
                    sources[value["url"]] = value
                for child in value.values():
                    enumerate_sources(child)
            elif isinstance(value, list):
                for child in value:
                    enumerate_sources(child)
        for record in manifest["files"]:
            if not verified(payload_target(root, record["destination"]), record) and record.get("fetch"):
                enumerate_sources(record["fetch"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetcher.url, sources.values()))
        for record in manifest["files"]:
            relative = Path(record["destination"])
            target = payload_target(root, record["destination"])
            if record.get("kind") == "git-history":
                if not verified(target, record):
                    acquire_git_history(record, target)
                continue
            if verified(target, record) or not record.get("fetch"):
                continue
            try:
                data = fetcher.render(record["fetch"])
                if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise ValueError("Reconstructed input differs from its frozen size/SHA-256")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_file = target.with_name("." + target.name + ".fetching")
                temporary_file.write_bytes(data)
                temporary_file.replace(target)
            except Exception as error:
                raise ValueError(f"PUBLIC INPUT FETCH FAILED [{manifest['task']}]: "
                                 f"{relative}; {error}; no replacement bytes permitted") from error


def check(root):
    root = Path(root).resolve()
    manifest = public_manifest(root)
    absent = []
    for record in manifest["files"]:
        if record.get("scope", "public") != "public" or record.get("kind", "file") not in ("file", "git-history", "repo-lfs"):
            raise ValueError("Public guard rejects private/unknown payload kinds")
        relative = Path(record["destination"])
        target = payload_target(root, record["destination"])
        if record.get("kind") in ("git-history", "repo-lfs"):
            if not verified(target, record):
                absent.append(str(relative))
            continue
        if (not target.is_file() or target.stat().st_size != record["bytes"]):
            absent.append(str(relative))
            continue
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != record["sha256"]:
            absent.append(str(relative))
    if absent:
        raise ValueError(f"PAYLOAD PENDING [{manifest['task']}] (public): "
                         f"python3 tools/stage_payload.py --task {manifest['task']} "
                         "--from /path/to/payload\n" + "\n".join(absent))


if __name__ == "__main__":
    try:
        args = sys.argv[1:]
        fetch = "--fetch" in args
        args = [arg for arg in args if arg != "--fetch"]
        root = args[0] if args else Path(__file__).resolve().parent
        if fetch:
            acquire(root)
        check(root)
        manifest = public_manifest(root)
        if any(record.get("kind") == "git-history" for record in manifest.get("files", [])):
            print(json.dumps({"status": "ready", "git_tool": git_tool_receipt()}, sort_keys=True))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
