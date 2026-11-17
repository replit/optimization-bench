#!/usr/bin/env python3
"""Register an explicit canonical guard release and sync all current packages."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def release(identifier):
    guard = ROOT / "tools/payload_check.py"
    path = ROOT / "tools/payload_guard_versions.json"
    registry = json.loads(path.read_text())
    digest = hashlib.sha256(guard.read_bytes()).hexdigest()
    existing = next((v for v in registry["versions"] if v["id"] == identifier), None)
    if existing and existing["sha256"] != digest:
        raise ValueError("A released guard version is immutable; choose a new identifier")
    if not existing:
        registry["versions"].append({
            "id": identifier, "sha256": digest,
            "payload_kinds": ["file", "git-history", "repo-lfs"],
            "features": ["url-recipes-v1", "git-history-v1", "absolute-git-tool-receipt",
                         "public-only-cli-receipt", "recipe-output-byte-authentication",
                         "repo-lfs-public-staging", "bounded-public-http-retries",
                         "gzip-member-slice-v1"],
        })
    registry["current"] = identifier
    path.write_text(json.dumps(registry, indent=2) + "\n")
    for task in sorted((ROOT / "tasks").iterdir()):
        if (task / "environment/payload_check.py").is_file():
            shutil.copyfile(guard, task / "environment/payload_check.py")
    print(json.dumps({"version": identifier, "sha256": digest}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identifier")
    release(parser.parse_args().identifier)
