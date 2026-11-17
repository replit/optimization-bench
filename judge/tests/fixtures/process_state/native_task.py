"""Offline trusted adapter: CLI status and both complete streams are the result."""
import sys
from runner import Step, locked_cases, bundle_references, canonical_json

ENV = {"LC_ALL": "C", "TRUSTED_ADAPTER_ENV": "set"}
cases = locked_cases
references = bundle_references


def prepare(case, ctx):
    return [Step([sys.executable, str(ctx.build / "parser.py")]) for _ in range(2)]


def collect(case, ctx, results):
    return {case.id: canonical_json({"format": "cli-output-v1",
                                    "runs": [result.streams() for result in results]})}
