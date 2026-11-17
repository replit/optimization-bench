#!/usr/bin/env python3
"""CI checks for the protected caller, using only authenticated public source."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from native_provenance import authenticate_source, TASK


def main():
    source_lock = json.loads((TASK / "environment/source.lock.json").read_text())
    with tempfile.TemporaryDirectory(prefix="sqlfmt-native-tests-") as directory:
        root = Path(directory)
        archive = root / "original.tar.gz"
        urllib.request.urlretrieve(source_lock["archive_url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != source_lock["archive_sha256"]:
            raise ValueError("Pinned public original source checksum mismatch")
        source = root / "project"
        source.mkdir()
        expected = json.loads((TASK / "tests/fuzz/native/native-source.lock.json").read_text())["files_sha256"]
        with tarfile.open(archive) as tar:
            for member in tar:
                name = "/".join(member.name.split("/")[1:])
                if not member.isfile():
                    continue
                if name not in expected:
                    raise ValueError("Unexpected source archive entry")
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
        authenticate_source(source)
        driver = root / "driver"
        shutil.copytree(TASK / "environment/workload/native", driver)
        (driver / "go.mod").write_text(
            "module optimbench-sqlfmt-native\n\ngo 1.26.5\n\n"
            "require github.com/dimitri/sqlfmt v0.0.0\n\n"
            "replace github.com/dimitri/sqlfmt => ../project\n")
        env = {**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local",
               "GOPROXY": "off", "GOSUMDB": "off", "GOCACHE": str(root / "cache")}
        subprocess.run(["go", "test", "-count=1", "./..."], cwd=driver, env=env, check=True)
        subprocess.run(["go", "vet", "./..."], cwd=driver, env=env, check=True)


if __name__ == "__main__":
    main()
