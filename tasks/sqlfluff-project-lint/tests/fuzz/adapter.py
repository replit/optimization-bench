"""Contract-scoped SQLFluff cases; no private corpus or candidate answers."""
from __future__ import annotations

import copy
import hashlib
import json
import random

TASK = "sqlfluff-project-lint"
OPERATIONS = {
    "api_lint", "api_fix", "api_parse", "api_list_rules", "api_list_dialects",
    "cli_lint", "cli_parse", "cli_fix", "cli_stdin",
    "block_config_state", "frozen_match_result", "dialect_state",
}
DIALECTS = ("ansi", "postgres", "bigquery", "sqlite", "tsql")
SURFACE = {
    "api_lint": "sqlfluff:lint", "api_fix": "sqlfluff:fix",
    "api_parse": "sqlfluff:parse", "api_list_rules": "sqlfluff:list_rules",
    "api_list_dialects": "sqlfluff:list_dialects",
    "cli_lint": "sqlfluff.cli:lint", "cli_parse": "sqlfluff.cli:parse",
    "cli_fix": "sqlfluff.cli:fix", "cli_stdin": "sqlfluff.cli:lint-stdin",
    "block_config_state": "sqlfluff.utils.reflow.config:ReflowConfig.get_block_config",
    "frozen_match_result": "sqlfluff.core.parser.match_result:MatchResult.empty_at",
    "dialect_state": "sqlfluff.core.dialects:dialect_selector",
}
SEQUENCES = {
    "block_config_state": ["init", "get", "incorporate", "get", "other-type", "fresh-config", "get"],
    "frozen_match_result": ["init", "empty", "append", "empty"],
    "dialect_state": ["init", "select", "mutate", "select", "other-dialect", "select"],
}


def contract_for(op):
    return "sqlfluff-cli-exact-v1" if op.startswith("cli_") else "sqlfluff-public-api-v1"


def case_hash(case):
    """Hash content, excluding only the hash field itself."""
    data = {k: v for k, v in case.items() if k != "case_sha256"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def seal(case):
    value = copy.deepcopy(case)
    value["case_sha256"] = case_hash(value)
    return value


def generate(rng, budget):
    """Bounded grammar/property seeds; campaign time limits belong to controller."""
    if not isinstance(rng, random.Random):
        raise TypeError("rng must be random.Random")
    limit = budget if isinstance(budget, int) else budget.get("cases", 64)
    if type(limit) is not int or not 0 <= limit <= 10000:
        raise ValueError("case budget must be an integer in 0..10000")
    ops = sorted(OPERATIONS)
    for index in range(limit):
        op = ops[index % len(ops)]
        dialect = DIALECTS[index % len(DIALECTS)]
        columns = rng.sample(("a", "b", "c", "item_id", "amount"), rng.randint(1, 3))
        sql = f"SELECT {', '.join(columns)} FROM records WHERE amount >= {rng.randrange(8)};"
        domain = "success"
        if index % 7 == 3:
            sql = "SELECT ("
            domain = "documented-sql-error"
        elif index % 7 == 4:
            sql = "-- public grammar boundary\nSELECT 1;\n\n"
        elif index % 7 == 5:
            sql = "SELECT 1 UNION ALL SELECT 2;"
        if op.startswith("api_list") or op in ("block_config_state", "frozen_match_result", "dialect_state"):
            domain = "success"
        value = {
            "task": TASK, "operation": op, "surface_id": SURFACE[op],
            "contract_id": contract_for(op),
            "precondition_id": "sqlfluff-constructor-valid-v1",
            "input": {"sql": sql, "domain": domain},
            "options": {"dialect": dialect, "processes": (1, 2, 8)[(index // len(ops)) % 3]},
            "sequence": SEQUENCES.get(op, ["init", "call", "finish"]),
            "faults": [],
            "provenance": {"kind": "grammar-generated", "private_inputs_used": False},
        }
        yield seal(value)


def precondition(case, surface):
    """Unsupported/private states are rejected before either library arm runs."""
    if not isinstance(case, dict):
        return {"valid": False, "reason": "case must be an object"}
    op = case.get("operation")
    if case.get("task") != TASK or op not in OPERATIONS:
        return {"valid": False, "reason": "unknown task or operation"}
    if not all(isinstance(case.get(k), dict) for k in ("input", "options", "provenance")):
        return {"valid": False, "reason": "input, options and provenance must be objects"}
    if case.get("surface_id") != SURFACE[op]:
        return {"valid": False, "reason": "operation/surface mismatch"}
    try:
        digest = case_hash(case)
    except (ValueError, TypeError, UnicodeError):
        return {"valid": False, "reason": "case is not canonical JSON/UTF-8"}
    if case.get("case_sha256") != digest:
        return {"valid": False, "reason": "case content hash mismatch"}
    if case.get("contract_id") != contract_for(op) or case.get("precondition_id") != "sqlfluff-constructor-valid-v1":
        return {"valid": False, "reason": "unknown contract or precondition"}
    sql = case.get("input", {}).get("sql")
    if not isinstance(sql, str) or len(sql.encode("utf-8")) > 8192:
        return {"valid": False, "reason": "SQL must be bounded UTF-8 text"}
    if case.get("input", {}).get("domain") not in ("success", "documented-sql-error"):
        return {"valid": False, "reason": "unknown documented input domain"}
    options = case.get("options", {})
    if options.get("dialect") not in DIALECTS or options.get("processes") not in (1, 2, 8):
        return {"valid": False, "reason": "unmapped dialect/process option"}
    if case.get("faults") != []:
        return {"valid": False, "reason": "fault schedule not implemented for this initial profile"}
    if case.get("sequence") != SEQUENCES.get(op, ["init", "call", "finish"]):
        return {"valid": False, "reason": "unsupported state sequence"}
    if case.get("provenance", {}).get("private_inputs_used") is not False:
        return {"valid": False, "reason": "public-only provenance required"}
    return {"valid": True, "reason": "documented bounded API/CLI or constructor-valid state"}


def shrink(case):
    """Bounded candidates; controller must retain the original failure class."""
    if not precondition(case, None)["valid"]:
        return
    if case["operation"] in ("block_config_state", "frozen_match_result", "dialect_state"):
        return  # Do not erase required interleave/reset transitions.
    sql = case["input"]["sql"]
    choices = (["SELECT ("] if case["input"]["domain"] == "documented-sql-error"
               else ["SELECT 1;", "-- boundary\nSELECT 1;"])
    for text in choices:
        if text == sql:
            continue
        value = copy.deepcopy(case)
        value["input"]["sql"] = text
        value = seal(value)
        if precondition(value, None)["valid"]:
            yield value


def compare(baseline, candidate, case):
    """Only contractual observations; diagnostics and build identities may differ."""
    if not precondition(case, None)["valid"]:
        raise ValueError("cannot compare a case outside its precondition")
    for arm, value in (("baseline", baseline), ("candidate", candidate)):
        if not isinstance(value, dict) or value.get("format") != "optimbench-fuzz-observation-v1":
            raise ValueError(f"invalid {arm} observation transport")
        if not all(k in value for k in ("identity", "termination", "outcome", "effects", "diagnostics")):
            raise ValueError(f"incomplete {arm} observation")
        if not all(isinstance(value[k], dict) for k in
                   ("identity", "termination", "outcome", "effects", "diagnostics")):
            raise ValueError(f"malformed {arm} observation objects")
        if value["termination"].get("kind") not in ("return", "exception", "panic", "signal", "timeout"):
            raise ValueError(f"unknown {arm} termination kind")
        if value["outcome"].get("kind") not in ("ok", "error"):
            raise ValueError(f"unknown {arm} outcome kind")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(f"non-JSON {arm} observation") from None
        identity = value["identity"]
        if (identity.get("task"), identity.get("surface_id"), identity.get("case_sha256")) != (
                TASK, case["surface_id"], case["case_sha256"]):
            raise ValueError(f"unbound {arm} observation identity")
    differences = []
    for field in ("termination", "outcome", "effects"):
        if baseline[field] != candidate[field]:
            differences.append({"field": field, "baseline": baseline[field], "candidate": candidate[field]})
    return {"equal": not differences, "policy_id": case["contract_id"], "differences": differences}
