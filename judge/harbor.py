"""Legacy Harbor verifier entry (Harbor is no longer used; score with judge.score).

Fails closed without the verifier host's required run config.
"""
import argparse
import json
import os
from pathlib import Path

from . import heldout
from .verifier import main as verify
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=os.environ.get("JUDGE_RUN_CONFIG"))
    args = parser.parse_args()
    logs = Path(os.environ.get("JUDGE_LOG_DIR", "/logs/verifier"))
    logs.mkdir(parents=True, exist_ok=True)
    try:
        # Checkout preflight authenticates retained files and reference
        # declarations only. verify() acquires and authenticates all reference
        # bodies before executing the scored verifier.
        heldout.check(args.task / "tests", require_available=True, allow_missing_references=True)
        if not args.config:
            raise ValueError("Verifier host must provide JUDGE_RUN_CONFIG; see docs/judge.md")
        config = json.loads(args.config.read_text())
        if config.get("plumbing"):
            raise ValueError("Harbor scored verifier cannot use public plumbing mode")
        sys.argv = ["judge.verifier", "--task", str(args.task), "--output", str(logs / "result.json")]
        for name in ("candidate", "projects", "image", "cpuset", "cpu_class",
                     "calibration", "upstream_receipt", "pristine", "artifacts"):
            sys.argv.extend(["--" + name.replace("_", "-"), str(config[name])])
        if "reference_cache" in config:
            sys.argv.extend(["--reference-cache", str(config["reference_cache"])])
        code = verify()
    except (ValueError, OSError, KeyError) as error:
        (logs / "result.json").write_text(json.dumps({
            "status": "invalid run", "claimable": False, "harness_error": str(error)}, indent=2) + "\n")
        code = 2
    # Harbor reward is correctness, not an unreviewed performance claim.
    (logs / "reward.txt").write_text("1\n" if code == 0 else "0\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
