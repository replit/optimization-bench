#!/usr/bin/env python3
"""Image-build helper (not part of the worker's workload): fetch pinned archives, check them, unpack.

  prepare.py source  DEST   Black at the pinned commit (source.lock.json) -> DEST
  prepare.py projects DEST  Original project trees (public-workloads.lock.json) -> DEST/<case path>,
                            each checked file by file against its recorded inventory; a case whose
                            source is null is an empty project directory (the startup probe)
"""
import hashlib, json, os, shutil, sys, tarfile, tempfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fetch(url, expected, size=None):
    handle, name = tempfile.mkstemp(suffix=".tar.gz")
    with os.fdopen(handle, "wb") as out, urllib.request.urlopen(url, timeout=300) as response:
        shutil.copyfileobj(response, out)
    path = Path(name)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected or (size is not None and path.stat().st_size != size):
        raise SystemExit(f"archive integrity mismatch: {url}")
    return path


def unpack(archive, destination):
    if destination.exists():
        raise SystemExit(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        with tarfile.open(archive) as tar:
            tar.extractall(temporary, filter="data")
        roots = list(Path(temporary).iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise SystemExit("expected a single archive root")
        shutil.move(roots[0], destination)
    archive.unlink()


def inventory(root):
    files, links = {}, {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            links[relative] = os.readlink(path)
        elif path.is_file():
            with path.open("rb") as stream:
                files[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files, links


def main():
    what, dest = sys.argv[1], Path(sys.argv[2])
    if what == "source":
        pin = json.loads((HERE / "source.lock.json").read_text())["upstream"]
        unpack(fetch(pin["url"], pin["sha256"], pin.get("bytes")), dest)
    elif what == "projects":
        manifest = json.loads((HERE / "public-workloads.lock.json").read_text())
        for case in manifest["cases"]:
            target = dest / case["path"]
            pin = case["source"]
            if pin is None:
                target.mkdir(parents=True)
            else:
                unpack(fetch(pin["url"], pin["sha256"], pin.get("archive_bytes")), target)
            if inventory(target) != (case["files_sha256"], case.get("symlinks", {})):
                raise SystemExit(f"complete original project inventory mismatch: {case['id']}")
            for path in [target, *target.rglob("*")]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | (0o555 if path.is_dir() else 0o444))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
