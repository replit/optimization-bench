#!/usr/bin/env python3
"""Authenticate both repository declarations and externally staged private bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

TASK = Path(__file__).resolve().parents[3]
ROOT = TASK.parents[1]
sys.path.insert(0,str(ROOT))
from judge import heldout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    # Static source packet forbids retaining even correct private bytes.
    lock = heldout.check(TASK/"tests",require_available=True,allow_missing_references=True)
    total = 0
    with tempfile.TemporaryDirectory(prefix="serde-heldout-realism-") as temporary:
        view = Path(temporary)/TASK.name
        shutil.copytree(TASK/"tests",view/"tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(TASK/"payload.json",view/"payload.json")
        for name,entry in lock["references"].items():
            source = args.cache/"files"/entry["sha256"]
            assert source.stat().st_size == entry["bytes"]
            with source.open("rb") as stream:
                assert hashlib.file_digest(stream,"sha256").hexdigest() == entry["sha256"]
            target = view/"tests"/name
            target.parent.mkdir(parents=True,exist_ok=True)
            os.link(source,target)
            total += entry["bytes"]
        heldout.check(view/"tests",require_available=True)
    report = {
        "task":TASK.name,"repository_declarations_passed":True,
        "private_external_content_lock_passed":True,
        "private_files":len(lock["references"]),"private_bytes":total,
        "private_input_bodies_retained_in_source_worktree":False,
        "timing_performed":False,
    }
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
