"""Download the pinned SQLGlot source, checking the complete archive.

SQLGlot's TPC-H/TPC-DS optimizer fixtures and its parse benchmark script
(benchmarks/parse.py, which embeds a TPC-H query) are TPC material and are removed
before the tree leaves this temporary directory, so no image layer contains them.
Nothing in the build, the workload or the upstream test gate uses either.
"""
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

pin = json.loads(Path(__file__).with_name("source.lock.json").read_text())["upstream"]
with tempfile.TemporaryDirectory() as tmp:
    archive = Path(tmp) / "source.tar.gz"
    with urllib.request.urlopen(pin["url"], timeout=300) as response, archive.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    assert archive.stat().st_size == pin["bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == pin["sha256"]
    with tarfile.open(archive) as tar:
        tar.extractall(Path(tmp) / "source", filter="data")
    roots = list((Path(tmp) / "source").iterdir())
    assert len(roots) == 1
    for fixture in ("tests/fixtures/optimizer/tpc-h", "tests/fixtures/optimizer/tpc-ds"):
        assert (roots[0] / fixture).is_dir()
        shutil.rmtree(roots[0] / fixture)
    (roots[0] / "benchmarks/parse.py").unlink()
    Path("/app").mkdir(exist_ok=True)
    shutil.move(roots[0], "/app/sqlglot")
