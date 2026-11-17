#!/usr/bin/env python3
"""Run the unchanged A/A tool with verifier-only fetch acquisition when needed."""
import argparse
from contextlib import nullcontext
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from judge import heldout
from tools import qualify_aa


def main():
    if any(arg in sys.argv[1:] for arg in ("--help", "-h")):
        return qualify_aa.main()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--scope", choices=("public", "heldout"), required=True)
    args, rest = parser.parse_known_args()
    acquire = heldout.materialized_task(args.task) if args.scope == "heldout" else nullcontext(args.task)
    with acquire as task:
        sys.argv = [sys.argv[0], "--task", str(task), "--scope", args.scope, *rest]
        return qualify_aa.main()


if __name__ == "__main__":
    raise SystemExit(main())
