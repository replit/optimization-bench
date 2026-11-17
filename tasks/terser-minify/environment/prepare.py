#!/usr/bin/env python3
"""Fetch and authenticate the pinned Terser archive at image build time."""
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

pin = json.loads(Path(__file__).with_name("source.lock.json").read_text())["upstream"]
destination = Path("/app/terser")
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archive = root / "source.tar.gz"
    with urllib.request.urlopen(pin["url"], timeout=300) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    assert archive.stat().st_size == pin["bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == pin["sha256"]
    with tarfile.open(archive) as source:
        # The pinned Node base has Python 3.11 without tarfile's filter keyword.
        # Reject links, devices and escaping paths before extracting ordinary files.
        for member in source:
            relative = Path(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert member.isfile() or member.isdir()
        source.extractall(root / "source")
    children = list((root / "source").iterdir())
    assert len(children) == 1
    shutil.move(children[0], destination)
