"""Finite constructor-valid public API cases; no candidate-label branches."""
import hashlib
import json

if __package__:
    from .adapter import TASK
else:
    from adapter import TASK

DOMAIN = "sqlfluff-rc-public-constructor-domain-v1"
CONTRACT = "sqlfluff-public-api-state-and-files-v1"
LINTER = "sqlfluff.core.linter.linter:Linter"


def seal(value):
    # Match the frozen central case_digest commitment, including Unicode escaping.
    value["case_sha256"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    return value


def case(operation, name, sql, dialect="ansi", fix=False, instance="primary", fault=None):
    symbol = (".parse_string" if operation == "parse" else
              ".lint_paths" if fault in ("unreadable", "missing", "parse-violation") else
              ".lint_string")
    symbol_id = LINTER + symbol
    if operation == "file" and fault not in ("unreadable", "missing", "parse-violation"):
        symbol_id = "sqlfluff.core.linter.linted_file:LintedFile.persist_tree"
    return seal({
        "task": TASK, "surface_id": symbol_id, "contract_id": CONTRACT,
        "precondition_id": DOMAIN, "operation": operation, "name": name,
        "input": {"sql": sql},
        "options": {"dialect": dialect, "fix": fix, "instance": instance,
                    "rules": "LT01,LT12,CP01", "templater": "raw",
                    "encoding": "ascii" if fault == "ascii-write" else "utf-8"},
        "sequence": ["construct-public-config", "call-public-method",
                     "observe-public-value-and-caller-flags"],
        "faults": [] if fault is None else [fault],
        "provenance": {"kind": "public-constructor-and-documented-file-API",
                       "private_inputs_used": False},
    })


def repeat_cases():
    return [
        case("lint", "ansi-fix-A", "select a,b FROM records\n", fix=True),
        case("lint", "postgres-B", "SELECT a::int FROM records\n", dialect="postgres"),
        case("parse", "bigquery-C", "SELECT SAFE_CAST(a AS INT64) FROM records\n", dialect="bigquery"),
        case("lint", "fresh-instance-D", "select a,b FROM records\n", fix=True, instance="fresh"),
    ]


def stream_cases():
    sql = "select  'café' as value\n"
    return [
        case("file", "persist-utf8", sql, fix=True),
        case("file", "persist-ascii-failure", sql, fix=True, fault="ascii-write"),
        case("file", "unreadable-input", sql, fault="unreadable"),
        case("file", "missing-input", sql, fault="missing"),
        case("file", "returned-parse-violation", "SELECT (", fix=True, fault="parse-violation"),
    ]


SCHEDULE = (0, 1, 0, 2, 0, 3, 1, 2, 3, 0)


def surface(case):
    return {
        "id": case["surface_id"], "visibility": "public",
        "contract_id": CONTRACT, "precondition_id": DOMAIN,
        "oracle": {"baseline_terminations": ["return", "exception"],
                   "baseline_exit_codes": [0],
                   "required_error_fields": {"class": "nonempty_string", "message": "string"}},
    }


def precondition(case, declared_surface=None):
    if not isinstance(case, dict) or case.get("case_sha256") != seal(
            {k: v for k, v in case.items() if k != "case_sha256"})["case_sha256"]:
        return {"valid": False, "reason": "case content commitment differs"}
    if case not in repeat_cases() + stream_cases():
        return {"valid": False, "reason": "not an exact registered public constructor/file case"}
    expected = surface(case)
    if declared_surface is not None and any(declared_surface.get(k) != expected[k] for k in
                                            ("id", "visibility", "contract_id", "precondition_id")):
        return {"valid": False, "reason": "public API surface binding differs"}
    return {"valid": True, "reason": "documented public API, valid constructor and explicit owned file fault"}


def compare(before, after, case):
    if not precondition(case)["valid"]:
        raise ValueError("unsupported public case")
    differences = [
        {"field": field, "baseline": before[field], "candidate": after[field]}
        for field in ("termination", "outcome", "effects") if before[field] != after[field]
    ]
    return {"equal": not differences, "policy_id": CONTRACT, "differences": differences}
