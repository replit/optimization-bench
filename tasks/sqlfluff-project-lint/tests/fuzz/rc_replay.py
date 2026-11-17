#!/usr/bin/env python3
"""Invoke the unmodified shared RC controller with source-bound task receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, required=True)
    parser.add_argument("--framework-revision", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", default="0")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("fresh receipt directory required")
    framework = args.framework.resolve()
    sys.path.insert(0, str(framework / "tools/runner"))
    sys.path.insert(0, str(framework))
    from judge import fuzzing_generic
    from judge.verifier import image_id
    from judge.source_policy import snapshot, digest

    # Refuse an accidentally imported host checkout instead of silently using it.
    if not Path(fuzzing_generic.__file__).resolve().is_relative_to(framework):
        raise ValueError("shared classifier imported from the wrong framework")
    task = Path(__file__).resolve().parents[2]
    policy = json.loads((task / "release.json").read_text())["source_policy"]
    sources = {
        arm: digest(snapshot(getattr(args, arm + "_source"), policy, trusted_pristine=arm == "baseline"))
        for arm in ("baseline", "candidate")
    }
    result = fuzzing_generic.check(task, args.baseline, args.candidate,
                                  image_id(args.image), args.cpuset, args.out,
                                  sources=sources)
    hashes = lambda root, names: {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
    }
    provenance = {
        "format": "optimbench-sqlfluff-canonical-rc-replay-v1",
        "framework_revision": args.framework_revision,
        "framework_files": hashes(framework, [
            "judge/fuzzing_generic.py", "judge/fuzzing_contract.py",
            "judge/verifier.py", "tools/runner/docker_executor.py", "tools/runner/runner.py",
        ]),
        "source_sha256": sources,
        "task_files": hashes(task / "tests/fuzz", [
            "adapter.py", "driver.py", "rc_hooks.py", "rc_driver.py", "rc_replay.py",
            "rc_cli.py", "public-scope.json",
        ]),
        "command": sys.argv,
        "full_fuzz_qualification": False, "timing_performed": False,
        "other_generic_gates": "not supplied by this aliasing-only replay",
        "memory_mode": "report-only; no verdict impact",
    }
    (args.out / "replay-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"canonical_status": result["status"],
                      "aliasing_status": result["gates"]["aliasing"]["status"],
                      "source_sha256": sources}, indent=2))


if __name__ == "__main__":
    main()
