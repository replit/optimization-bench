#!/usr/bin/env python3
"""Recreate the pinned unmodified source tree and deterministic baseline Git."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

here = Path(__file__).resolve().parent
pin = json.loads((here / "source.lock.json").read_text())
source = Path("/app/sqlite")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archive = root / "source.tar.gz"
    with urllib.request.urlopen(pin["upstream"]["url"], timeout=300) as response:
        archive.write_bytes(response.read())
    assert archive.stat().st_size == pin["upstream"]["bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == pin["upstream"]["sha256"]
    with tarfile.open(archive) as tar:
        tar.extractall(root / "unpack", filter="data")
    roots = list((root / "unpack").iterdir())
    assert len(roots) == 1
    shutil.move(roots[0], source)
env = dict(os.environ, GIT_AUTHOR_NAME="optimbench", GIT_AUTHOR_EMAIL="optimbench@invalid",
           GIT_COMMITTER_NAME="optimbench", GIT_COMMITTER_EMAIL="optimbench@invalid",
           GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
           GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
def git(*args):
    return subprocess.check_output(["git", "-C", str(source), *args], env=env, text=True).strip()
git("-c", "init.defaultBranch=optimbench-baseline", "init", "-q")
git("-c", "core.autocrlf=false", "-c", "core.filemode=true", "add", "-A")
git("-c", "commit.gpgsign=false", "commit", "-qm", "SQLite distinct facets (pinned baseline)")
assert git("rev-parse", "HEAD") == pin["baseline_git_commit"]
assert git("rev-parse", "HEAD^{tree}") == pin["baseline_git_tree"]
