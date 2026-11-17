#!/usr/bin/env python3
"""Untimed, deterministic per-case derivations from authenticated canonical histories.

Only the image's protected pristine Git (built from the unchanged source at image
build) runs here, never the build under test. Outputs are written to the fresh
case scratch before any timed step:
- blobs: original blob bytes for merge-file triples, each re-hashed to its object ID;
- warm: canonical pack/index files read into the page cache (no Git execution).
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

PRISTINE_GIT = "/app/baseline-build/project/git"
BUFFER_BYTES = 8 * 1024 * 1024
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8", "TZ": "UTC",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ATTR_NOSYSTEM": "1",
       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*",
       "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"}


def inside(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Derived input path outside its protected root: " + str(path))
    return path


def history_objects(path, inputs):
    """Accept only <inputs>/histories/<id>/.git/objects of an authenticated history."""
    path = inside(path, Path(inputs) / "histories")
    if path.name != "objects" or path.parent.name != ".git" or path.parent.parent.parent != Path(inputs).resolve() / "histories":
        raise ValueError("Expected a canonical history object store")
    return path


def blobs(git, objects, files, scratch):
    """Extract every listed blob once and verify its bytes against the object ID."""
    wanted = sorted({oid for oid, _ in files})
    process = subprocess.Popen([git, "--git-dir=" + str(objects.parent), "cat-file", "--batch"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=dict(ENV))
    data = {}
    try:
        for oid in wanted:
            process.stdin.write(oid.encode() + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[0] != oid.encode() or header[1] != b"blob":
                raise ValueError("Missing merge-file blob " + oid)
            size = int(header[2])
            body = process.stdout.read(size)
            if len(body) != size or process.stdout.read(1) != b"\n":
                raise ValueError("Truncated merge-file blob " + oid)
            if hashlib.sha1(b"blob %d\0" % size + body).hexdigest() != oid:
                raise ValueError("Merge-file blob does not match its object ID " + oid)
            data[oid] = body
        process.stdin.close()
        if process.wait(timeout=600):
            raise ValueError("Pristine cat-file failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    count = 0
    for oid, name in files:
        target = inside(Path(scratch) / name, scratch)
        if target.exists() or target.is_symlink():
            raise ValueError("Derived merge-file input already exists")
        target.write_bytes(data[oid])
        target.chmod(0o666)
        count += len(data[oid])
    return count


def warm(repositories, inputs):
    buffer = bytearray(BUFFER_BYTES)
    total = files = 0
    for repository in repositories:
        pack = inside(Path(repository), Path(inputs) / "histories") / ".git/objects/pack"
        for path in sorted(pack.glob("pack-*.pack")) + sorted(pack.glob("pack-*.idx")):
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb", buffering=0) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Expected a regular canonical object file")
                while size := stream.readinto(buffer):
                    total += size
            files += 1
    return files, total


def apply(plan, scratch, inputs, git=PRISTINE_GIT):
    scratch, inputs = Path(scratch).resolve(), Path(inputs).resolve()
    if set(plan) - {"blobs", "warm"}:
        raise ValueError("Unknown derivation")
    receipt = {"blob_files": 0, "blob_bytes": 0}
    files, total = warm(plan.get("warm", []), inputs)
    receipt.update(warm_files=files, warm_bytes=total)
    for group in plan.get("blobs", []):
        objects = history_objects(group["objects"], inputs)
        receipt["blob_bytes"] += blobs(git, objects, group["files"], scratch)
        receipt["blob_files"] += len(group["files"])
    return receipt


if __name__ == "__main__":
    import sys
    print(json.dumps(apply(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2], sys.argv[3]),
                     sort_keys=True))
