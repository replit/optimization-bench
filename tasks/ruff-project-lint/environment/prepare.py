#!/usr/bin/env python3
"""Build-time acquisition: pinned sources, Rust distribution and complete Git trees."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent


def fetch(pin, dest):
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "archive"
        with urllib.request.urlopen(pin["archive_url"], timeout=300) as source, archive.open("wb") as out:
            shutil.copyfileobj(source, out)
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != pin["archive_sha256"]:
            raise ValueError("Archive SHA-256 mismatch")
        unpack = Path(temporary) / "unpack"
        unpack.mkdir()
        with tarfile.open(archive) as tar:
            # This base's Python predates extraction filters. Archives are pinned
            # and checked above; still reject escaping paths and link targets.
            for member in tar.getmembers():
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe archive path")
                if member.issym() or member.islnk():
                    link = Path(member.linkname)
                    link_target = unpack / (path.parent / link if member.issym() else link)
                    if link.is_absolute() or os.path.commonpath([unpack.resolve(), link_target.resolve()]) != str(unpack.resolve()):
                        raise ValueError("Unsafe archive link")
            tar.extractall(unpack)
        roots = list(unpack.iterdir())
        if len(roots) != 1:
            raise ValueError("Expected one archive root")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(roots[0], dest)


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null",
         "-C", str(root), *args]).decode().strip()


def projects(dest):
    lock = json.loads((HERE / "public-workloads.lock.json").read_text())
    trees = json.loads((HERE / "public-trees.json").read_text())["cases"]
    for case in lock["cases"]:
        target = dest / case["name"]
        target.mkdir(parents=True)
        git(target, "init", "--quiet")
        git(target, "remote", "add", "origin", case["pin"]["repository"])
        git(target, "fetch", "--quiet", "--depth=1", "origin", case["pin"]["commit"])
        git(target, "checkout", "--quiet", "--detach", case["pin"]["commit"])
        manifest = trees[case["name"]]
        assert git(target, "rev-parse", "HEAD") == manifest["commit"]
        assert git(target, "rev-parse", "HEAD^{tree}") == manifest["git_tree"]
        actual = {p.relative_to(target).as_posix() for p in target.rglob("*")
                  if ".git" not in p.relative_to(target).parts and (p.is_file() or p.is_symlink())}
        assert actual == set(manifest["files"])
        for name, expected in manifest["files"].items():
            path = target / name
            assert not path.is_symlink()
            data = path.read_bytes()
            assert len(data) == expected["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected["sha256"]
            assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == expected["git_blob"]
            path.chmod(0o755 if expected["git_mode"] == "100755" else 0o644)


def main():
    mode, dest = sys.argv[1], Path(sys.argv[2])
    lock = json.loads((HERE / "source.lock.json").read_text())
    if mode == "source":
        fetch(lock["ruff"], dest)
        assert hashlib.sha256((dest / "Cargo.lock").read_bytes()).hexdigest() == lock["cargo_lock_sha256"]
    elif mode == "rust":
        with tempfile.TemporaryDirectory() as temp:
            installer = Path(temp) / "rust"
            fetch(lock["rust_distribution"], installer)
            subprocess.run(["sh", str(installer / "install.sh"), "--prefix=" + str(dest),
                            "--disable-ldconfig", "--components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu"],
                           check=True)
    elif mode == "projects":
        projects(dest)
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
