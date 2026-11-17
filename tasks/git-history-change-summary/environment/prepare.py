#!/usr/bin/env python3
"""Build-context acquisition for mined tasks; no benchmark execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def fetch(record, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raw = destination.read_bytes()
        if hashlib.sha256(raw).hexdigest() == record["sha256"]:
            return
    try:
        if "content_utf8" in record:
            raw = record["content_utf8"].encode()
        else:
            with urllib.request.urlopen(record["url"], timeout=300) as response:
                raw = response.read()
        if ("bytes" in record and len(raw) != record["bytes"]
                or hashlib.sha256(raw).hexdigest() != record["sha256"]):
            raise ValueError("size or SHA-256 mismatch")
        destination.write_bytes(raw)
    except Exception as error:
        raise RuntimeError("Required pinned artifact unavailable or corrupt: "
                           + record.get("url", str(destination))
                           + ". Build cannot continue; do not substitute bytes.") from error


def prepare(environment, app):
    lock = json.loads((environment / "source.lock.json").read_text())
    library = json.loads((environment / "layout.json").read_text())["library"]
    destination = app / library
    app.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        archive = temporary / "source.tar.gz"
        fetch(lock["upstream"], archive)
        with tarfile.open(archive) as stream:
            # Pinned Go/GCC bases carry Python 3.11 without filter=. Validate
            # all archive paths/link destinations before extracting there.
            root = temporary / "unpacked"
            for member in stream.getmembers():
                relative = Path(member.name)
                if (relative.is_absolute() or ".." in relative.parts
                        or not (member.isfile() or member.isdir()
                                or member.issym() or member.islnk())):
                    raise ValueError("Unsafe source archive member")
                if member.issym() or member.islnk():
                    target = (root / relative.parent / member.linkname).resolve()
                    if not target.is_relative_to(root.resolve()):
                        raise ValueError("Unsafe source archive link")
            if hasattr(tarfile, "data_filter"):
                stream.extractall(root, filter="data")
            else:
                stream.extractall(root)
        roots = list((temporary / "unpacked").iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError("Expected one archive root")
        shutil.move(roots[0], destination)
    for record in lock.get("dependencies", []):
        if "path" in record:
            fetch(record, app / "offline-module-proxy" / record["path"])
    # Canonical source staging and commit, independent of host identity/date.
    env = dict(os.environ, GIT_AUTHOR_NAME="optimbench",
               GIT_AUTHOR_EMAIL="optimbench@invalid", GIT_COMMITTER_NAME="optimbench",
               GIT_COMMITTER_EMAIL="optimbench@invalid",
               GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
               GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(destination), *args], env=env).decode().strip()
    git("-c", "init.defaultBranch=optimbench-baseline", "init", "-q")
    git("-c", "core.autocrlf=false", "-c", "core.filemode=true", "add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-qm", library + " (pinned baseline)")
    return {"baseline_git_commit": git("rev-parse", "HEAD"),
            "baseline_git_tree": git("rev-parse", "HEAD^{tree}")}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("/app"))
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parent, args.app)))
