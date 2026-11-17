#!/usr/bin/env python3
"""Add only the newly authored diagnostic set to the protected private package.

Reads existing metadata to preserve it. Never opens old evaluation input bodies.
Prints counts only. Native membership and full references never enter environment/.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

TASK = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    indent = 1 if path.name == "public-workloads.lock.json" else 2
    path.write_text(json.dumps(value, indent=indent) + "\n")


def main(private):
    inputs = private / "inputs"
    new_manifest = json.loads((inputs / "native-batch.json").read_text())
    assert new_manifest["edition"] == "diagnostic-v1"
    assert (private / "audit.json").is_file()
    lock_path = TASK / "tests/heldout.lock.json"
    lock = json.loads(lock_path.read_text())
    original = dict(lock["files"])
    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    origins = json.loads((private / "origins.json").read_text())
    native_origins = private / "native-origins.json"
    dump(native_origins, {k: origins[k] for k in ("archive_sha256", "commit", "native")})
    additions = []
    for doc in new_manifest["docs"]:
        source = inputs / "files" / doc["sha256"]
        assert sha(source) == doc["sha256"] and source.stat().st_size == doc["bytes"]
        additions.append((source, TASK / "tests/heldout/files" / doc["sha256"]))
    additions += [
        (inputs / "native-batch.json", TASK / "tests/heldout/native-batch.json"),
        (private / "reference.bin", TASK / "tests/heldout/references/native-batch.bin"),
        (native_origins, TASK / "tests/heldout/native-origins.json"),
    ]
    for source, dest in additions:
        dest.parent.mkdir(parents=True, exist_ok=True)
        assert not dest.exists(), "Refuse replacing an existing private object"
        shutil.copy2(source, dest)
        relative = dest.relative_to(TASK)
        key = dest.relative_to(TASK / "tests").as_posix()
        assert key not in lock["files"]
        lock["files"][key] = sha(dest)
        payload["files"].append({
            "scope": "heldout", "name": "native-addition-" + str(len(payload["files"])),
            "destination": relative.as_posix(), "bytes": dest.stat().st_size,
            "sha256": sha(dest),
        })
    assert all(lock["files"][key] == value for key, value in original.items())
    # Availability is checked separately after original LFS bodies are restored.
    lock["status"] = "unavailable"
    lock["native_addition"] = {
        "edition": "diagnostic-v1", "qualification": False,
        "freeze_limit": "Distinct diagnostic inputs only. Corpus sizing, complete global overlap audit, weighting and qualification remain open."}
    dump(lock_path, lock)
    dump(payload_path, payload)
    public_lock_path = TASK / "environment/public-workloads.lock.json"
    public_lock = json.loads(public_lock_path.read_text())
    public = json.loads((TASK / "environment/workload/inputs/native-batch.json").read_text())
    assert not any(row["id"] == "native-batch" for row in public_lock["cases"])
    public_lock["cases"].append({
        "id": "native-batch", "split": "native-diagnostic", "expected_exit": 0,
        "path": "environment/workload/inputs/files",
        "files_sha256": {row["sha256"]: row["sha256"] for row in public["docs"]},
        "source": {"kind": "manifest", "manifest": "environment/workload/inputs/native-batch.json",
                   "committed": True}})
    dump(public_lock_path, public_lock)
    print(json.dumps({"new_private_objects": len(additions), "existing_private_commitments_preserved": True,
                      "public_native_documents": len(public["docs"]),
                      "hidden_membership_printed": False, "lock_requires_authentication": True}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
