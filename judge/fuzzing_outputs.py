"""Declared CLI output and recognized OS-error identities; raw evidence stays intact."""
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re

from . import fuzzing_contract as contract
from .cli_stderr import stderr_contract


class OutputContractError(ValueError):
    pass


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise OutputContractError("Duplicate JSON key")
            value[key] = item
        return value
    def constant(_):
        raise OutputContractError("Nonfinite JSON value")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise OutputContractError("Output violates declared strict JSON contract") from error


def output_value(raw, rule):
    if rule["kind"] == "exact-bytes":
        return {"kind": "exact-bytes", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    # Numeric means an explicitly declared numeric JSON tree, never guessed
    # from a filename, extension or arbitrary binary representation.
    return {"kind": rule["kind"], "value": strict_json(raw)}


def equal_output(left, right, rule):
    if rule["kind"] != "numeric":
        return contract.typed_equal(left, right)
    def numeric(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, float):
            return math.isclose(a, b, rel_tol=rule["rtol"], abs_tol=rule["atol"])
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(numeric(a[key], b[key]) for key in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(numeric(x, y) for x, y in zip(a, b))
        return contract.typed_equal(a, b)
    return numeric(left, right)


def path_identity(path, spec, internal_temp):
    if path is None:
        return {"role": "unreported"}
    name = path.removeprefix("/scratch/work/")
    declared = [(str(value), "output") for value in spec.get("required_outputs", ())]
    declared += [(str(value), "input") for value in spec.get("watch_inputs", ())]
    for value, role in declared:
        if name == value:
            return {"role": role, "path": value}
    if internal_temp:
        actual = PurePosixPath(name)
        for value in spec.get("required_outputs", ()):
            output = PurePosixPath(value)
            if actual.parent != output.parent:
                continue
            for prefix in (output.name, output.stem + "."):
                if actual.name.startswith(prefix):
                    remainder = actual.name[len(prefix):]
                    if output.suffix and remainder.endswith(output.suffix):
                        remainder = remainder[:-len(output.suffix)]
                    if re.fullmatch(r"[A-Za-z0-9_-]{3,}", remainder):
                        return {"role": "internal-temp", "for_output": str(output)}
    # Unknown paths are not silently classified as internal temporaries.
    return {"role": "user-visible-other", "path": name}


def error_identity(raw, spec):
    from tools.fuzz_api_faults.os_errors import parse_os_error
    facts = parse_os_error(raw.decode("utf-8", errors="surrogateescape"))
    if facts is None:
        return stderr_contract(raw)
    return {"version": "os-error-identity-v1", "class": facts["exception_class"],
            "errno": facts["errno"], "code": facts["code"],
            "operation": facts["operation_category"],
            "path": path_identity(facts["path"], spec, facts["internal_temp_frame"]),
            "destination": path_identity(facts.get("destination_path"), spec, False)}


def file_tree(tree, rule, root, read_stream, outputs_only=None):
    def visit(value, name=""):
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        if value.get("kind") == "file":
            result = {key: copy.deepcopy(item) for key, item in value.items() if key != "artifact"}
            selected = outputs_only is None or any(
                name == path or name.startswith(path.rstrip("/") + "/") for path in outputs_only)
            if rule["kind"] != "exact-bytes" and selected:
                if not value.get("artifact"):
                    raise ValueError("Declared file comparator needs complete authenticated file contents")
                raw = read_stream(value, root)
                result.pop("bytes", None)
                result.pop("sha256", None)
                result["content"] = output_value(raw, rule)
            return result
        return {key: visit(item, key) for key, item in value.items()}
    return visit(tree)


def equal_cli(left, right, policy):
    if left.get("declared_output_contract_violation") or right.get("declared_output_contract_violation"):
        return contract.typed_equal(left, right)
    special = {"stdout", "input_after", "output_after", "required_outputs"}
    if not contract.typed_equal({k: v for k, v in left.items() if k not in special},
                                {k: v for k, v in right.items() if k not in special}):
        return False
    return (equal_output(left["stdout"], right["stdout"], policy["stdout"])
            and all(equal_output(left[key], right[key], policy["files"])
                    for key in ("input_after", "output_after", "required_outputs")))
