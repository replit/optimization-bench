"""Fetch and verify source and original project archives during image construction."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import urllib.parse

HERE = Path(__file__).resolve().parent


def unpack(pin, target):
    url = pin.get("url", pin.get("archive_url"))
    expected = pin.get("sha256", pin.get("archive_sha256"))
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "archive.tar.gz"
        with urllib.request.urlopen(url, timeout=300) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected, url
        size = pin.get("bytes", pin.get("archive_bytes"))
        assert size is None or archive.stat().st_size == size
        extracted = Path(tmp) / "extracted"
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        roots = list(extracted.iterdir())
        assert len(roots) == 1 and roots[0].is_dir()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(roots[0], target)


def main():
    unpack(json.loads((HERE / "source.lock.json").read_text())["upstream"], Path("/app/sqlfluff"))
    for case in json.loads((HERE / "public-workloads.lock.json").read_text())["cases"]:
        target = Path("/app/projects") / case["path"]
        pin = case["source"]
        if pin.get("url", pin.get("archive_url")):
            unpack(pin, target)
        else:
            # The original Almanac case is a declared chapter extraction, not a
            # whole-repository operation. Retain that exact authenticated tree.
            for name, expected in case["files_sha256"].items():
                path = target / name
                assert path.resolve().is_relative_to(target.resolve())
                path.parent.mkdir(parents=True, exist_ok=True)
                if "origins" in pin:
                    row = next(row for row in pin["files"] if row["path"] == name)
                    origin = next(item for item in pin["origins"] if item["prefix"] == row["origin"])
                    member = row["original_path"]
                    assert name == origin["prefix"] + "/" + member
                    assert len(origin["revision"]) == 40
                    url = (f"https://raw.githubusercontent.com/{origin['repository']}/"
                           f"{origin['revision']}/{urllib.parse.quote(member)}")
                    assert row["url"] == url and row["sha256"] == expected
                else:
                    url = f"https://raw.githubusercontent.com/{pin['repo']}/{pin['commit']}/{urllib.parse.quote(name)}"
                with urllib.request.urlopen(url, timeout=300) as response:
                    data = response.read()
                assert hashlib.sha256(data).hexdigest() == expected, name
                if "origins" in pin:
                    assert len(data) == row["bytes"]
                path.write_bytes(data)
        files, links = {}, {}
        for path in target.rglob("*"):
            name = path.relative_to(target).as_posix()
            if path.is_symlink():
                links[name] = os.readlink(path)
            elif path.is_file():
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert files == case["files_sha256"], case["id"]
        assert links == case.get("symlinks", {}), case["id"]


if __name__ == "__main__":
    main()
