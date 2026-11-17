"""Protected task output-comparator declarations; absence means exact bytes."""
import copy
import json
import math
from pathlib import Path

VERSION = "task-output-comparator-v1"
DEFAULT = {"version": VERSION, "stdout": {"kind": "exact-bytes"},
           "files": {"kind": "exact-bytes"}}


def load_output_comparator(task):
    settings = json.loads((Path(task) / "tests/judge.json").read_text())
    value = copy.deepcopy(settings.get("output_comparator", DEFAULT))
    if not isinstance(value, dict) or set(value) != {"version", "stdout", "files"} or value["version"] != VERSION:
        raise ValueError("Invalid protected output-comparator declaration")
    for stream in ("stdout", "files"):
        rule = value[stream]
        if not isinstance(rule, dict) or rule.get("kind") not in ("exact-bytes", "json-structural", "numeric"):
            raise ValueError("Unknown declared output comparator")
        expected = {"kind", "rtol", "atol"} if rule["kind"] == "numeric" else {"kind"}
        if set(rule) != expected:
            raise ValueError("Output comparator parameters must be explicit")
        if rule["kind"] == "numeric" and any(
                type(rule[key]) not in (int, float) or not math.isfinite(rule[key]) or rule[key] < 0
                for key in ("rtol", "atol")):
            raise ValueError("Invalid declared numeric tolerance")
    return value
