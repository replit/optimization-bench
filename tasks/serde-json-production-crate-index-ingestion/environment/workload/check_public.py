#!/usr/bin/env python3
"""Build-time independent full-result commitment check, no timer."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from task import strict_loads

root = Path(__file__).parent
manifest = json.loads((root/"inputs/manifest.json").read_text())
docs = [doc for batch in manifest["splits"]["public"] if batch.get("id", "distinct-registry-corpus") == "distinct-registry-corpus" for doc in batch["docs"]]
assert len(docs) == len({doc["sha256"] for doc in docs})
with tempfile.TemporaryDirectory(prefix="serde-public-correctness-") as temporary:
    work = Path(temporary)
    entries = []
    for doc in docs:
        path = root/"inputs/files"/doc["sha256"]
        raw = path.read_bytes()
        assert len(raw)==doc["bytes"] and hashlib.sha256(raw).hexdigest()==doc["sha256"]
        entries.append(doc["path"]+"\t"+str(path)+"\n")
    (work/"batch-list.tsv").write_text("".join(entries))
    result = subprocess.run([sys.argv[1],"--check-batch",str(work/"batch-list.tsv"),
                             str(work/"outputs")],capture_output=True,check=True)
    assert not result.stderr and not result.stdout
    assert {path.name for path in (work/"outputs").iterdir()} == {doc["path"]+".json" for doc in docs}
    for doc in docs:
        canonical = json.dumps(strict_loads((work/"outputs"/(doc["path"]+".json")).read_bytes()),sort_keys=True,
                               separators=(",",":"),ensure_ascii=True).encode()
        assert len(canonical)==doc["expect_len"]
        assert hashlib.sha256(canonical).hexdigest()==doc["expect_sha"]
print(f"All {len(docs)} distinct public results match frozen complete structured-JSON commitments; no timing")

# geojson-typed unit: exact complete flat renderings from the protected GeoJSON driver.
geo = [doc for batch in manifest["splits"]["public"] if batch.get("id") == "geojson-typed-corpus" for doc in batch["docs"]]
assert geo and len(geo) == len({doc["sha256"] for doc in geo})
with tempfile.TemporaryDirectory(prefix="serde-geojson-public-correctness-") as temporary:
    work = Path(temporary)
    entries = []
    for doc in geo:
        path = root/"inputs/files"/doc["sha256"]
        raw = path.read_bytes()
        assert len(raw)==doc["bytes"] and hashlib.sha256(raw).hexdigest()==doc["sha256"]
        entries.append(doc["path"]+"\t"+str(path)+"\n")
    (work/"batch-list.tsv").write_text("".join(entries))
    geojson = subprocess.run([str(Path(sys.argv[1]).parent/"prog-geojson"),"--check-batch",str(work/"batch-list.tsv"),
                              str(work/"outputs")],capture_output=True,check=True)
    assert not geojson.stderr and not geojson.stdout
    assert {path.name for path in (work/"outputs").iterdir()} == {doc["path"]+".bin" for doc in geo}
    for doc in geo:
        output = (work/"outputs"/(doc["path"]+".bin")).read_bytes()
        assert len(output)==doc["expect_len"]
        assert hashlib.sha256(output).hexdigest()==doc["expect_sha"]
print(f"All {len(geo)} distinct geojson-typed public results match frozen exact-byte commitments; no timing")
