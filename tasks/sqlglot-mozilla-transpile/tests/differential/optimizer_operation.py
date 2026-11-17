"""Observe process state after small representatives of all four operations."""
import copy
import dataclasses
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import warnings
from unittest.mock import patch


# Baseline-observed highlighting membership for three original SQLMesh probes,
# the documentation example and TPC-H 1. No allocation identifiers are frozen.
LINEAGE_API_PROBES = {
    "3c55b4f67b63ecc3502888b05e6e5084dbdca91bfee0d24b66a71188c1ece24c": 7,
    "7f2f9f8efb8773131f495665fa63a7ed8e9689c95bbae5882a458b510a8b6c88": 9,
    "7367d02063d05ef94e13c80bfe3af9a19164a6feebd334c98a0f779ceb7b0664": 13,
    "f4e51a012827d12c0f84e6a8069f13a714ecd28474a22a7af09083f570090f7c": 5,
    "64f8fe8351a3bf415e335260a9c9be4cb6565935bf890b82e4e27c61cb8958c0": 22,
}


def parents(expressions):
    from sqlglot import exp
    seen, pending = set(), list(expressions)
    while pending:
        parent = pending.pop()
        if id(parent) in seen:
            continue
        seen.add(id(parent))
        for key, value in parent.args.items():
            children = value if isinstance(value, list) else [value]
            for index, child in enumerate(children):
                if isinstance(child, exp.Expr):
                    assert child.parent is parent
                    assert child.arg_key == key
                    assert child.index == (index if isinstance(value, list) else None)
                    pending.append(child)


def contains_expression(node):
    return any(expression is node.expression for expression in node.source.walk())


def rejects(action):
    try:
        action()
    except AssertionError:
        return
    raise AssertionError("API corruption was accepted")


def highlight(node):
    assert contains_expression(node), "Public highlighting relationship changed"


def lineage_api(sqlglot):
    """Separate untimed API gate, not part of the performance contract."""
    from sqlglot.optimizer import qualify as qualify_module
    from sqlglot.optimizer.scope import build_scope
    spec = importlib.util.spec_from_file_location("protected_lineage_api", "/app/workload/driver/analysis_lineage.py")
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    for digest, expected_count in LINEAGE_API_PROBES.items():
        raw = (Path("/app/workload/inputs/files") / digest).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        case = json.loads(raw)
        schema = copy.deepcopy(case["schema"])
        caller = sqlglot.parse_one(case["sql"], read=case["dialect"], error_level=sqlglot.ErrorLevel.RAISE)
        before = caller.dump()
        with patch.object(qualify_module, "qualify", wraps=qualify_module.qualify) as call:
            working = job.qualify_query(caller, case["schema"], case["dialect"])
            assert working is call.call_args.args[0] and working is not caller
        qualified = working.dump()
        scope = build_scope(working)
        assert scope.expression is working
        roots = job.lineage_roots(working, scope, case["dialect"])
        assert caller.dump() == before and case["schema"] == schema
        assert working.dump() == qualified
        nodes, seen = [], set()
        for _, root in roots:
            for node in root.walk():
                if id(node) not in seen:
                    nodes.append(node)
                    seen.add(id(node))
        assert len(nodes) == expected_count and all(contains_expression(node) for node in nodes)
        parents([caller, working, *(node.source for node in nodes), *(node.expression for node in nodes)])
        child = working.selects[0]
        for attribute, invalid in (("parent", None), ("arg_key", "wrong"), ("index", 999)):
            saved = getattr(child, attribute)
            setattr(child, attribute, invalid)
            rejects(lambda: parents([working]))
            setattr(child, attribute, saved)
        parents([working])
        root = roots[0][1]
        broken = dataclasses.replace(root, expression=root.expression.copy())
        assert job.encode(job.graph_record([("x", root)])) == job.encode(job.graph_record([("x", broken)]))
        rejects(lambda: highlight(broken))
        bad_html = dataclasses.replace(broken, downstream=[]).to_html(dialect=case["dialect"], imports=False)
        good_html = dataclasses.replace(root, downstream=[]).to_html(dialect=case["dialect"], imports=False)
        assert "<b>" not in next(iter(bad_html.nodes.values()))["title"]
        assert "<b>" in next(iter(good_html.nodes.values()))["title"]

    logger = logging.getLogger("sqlglot")
    handlers, filters = list(logger.handlers), list(warnings.filters)
    reference = job.one_case(case)
    error = job.one_case({"id": "parse-error", "sql": "SELECT (", "dialect": "duckdb", "schema": {}})
    assert error["exception"] == "sqlglot.errors.ParseError" and job.one_case(case) == reference
    original = job.lineage_roots

    def incomplete_scope(query, scope, dialect):
        scope.subquery_scopes.clear()
        return original(query, scope, dialect)

    with patch.object(job, "lineage_roots", incomplete_scope):
        result = job.one_case({"id": "warning", "dialect": "duckdb",
                              "sql": "SELECT (SELECT MAX(id) FROM t) AS value", "schema": {"t": {"id": "INT"}}})
    assert len(result["logs"]) == 1 and result["logs"][0]["message"].startswith("Unknown subquery scope: ")

    def python_warning(query, scope, dialect):
        warnings.warn("protected warning-capture control", UserWarning)
        return original(query, scope, dialect)

    with patch.object(job, "lineage_roots", python_warning):
        result = job.one_case(case)
    assert result["python_warnings"] == [{"category": "builtins.UserWarning", "message": "protected warning-capture control"}]
    assert job.one_case(case) == reference
    assert logger.handlers == handlers and warnings.filters == filters


def run(sqlglot):
    from sqlglot.optimizer import RULES, optimize
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.qualify_columns import quote_identifiers

    # Preserve the original representative call.
    sqlglot.transpile("SELECT 1 + 2", read="postgres", write="sqlite")
    schema = {"x": {"id": "INT", "a": "INT"}, "y": {"id": "INT", "b": "INT"}}
    tree = sqlglot.parse_one(
        "SELECT x.a + 1 AS v FROM x JOIN y ON x.id = y.id WHERE y.b > 0",
        error_level=sqlglot.ErrorLevel.RAISE)
    normalize_identifiers(tree, dialect=None)
    quote_identifiers(tree, dialect=None)
    before_tree, before_schema = copy.deepcopy(tree.dump()), copy.deepcopy(schema)
    optimized = optimize(tree, schema=schema, dialect=None, rules=RULES,
                         infer_schema=True, quote_identifiers=True, db=None, catalog=None)
    assert tree.dump() == before_tree and schema == before_schema
    assert optimized is not tree
    # Force traversal, type access and output generation before state is read.
    result = optimized.sql(dialect=None, pretty=False)
    types = [node.type.sql() if node.type is not None else None for node in optimized.walk()]
    json.dumps({"sql": result, "tree": optimized.dump(), "types": types})
    from sqlglot.lineage import lineage
    graph = lineage("v", "SELECT a + 1 AS v FROM x", schema=schema, trim_selects=False)
    json.dumps([{"name": node.name, "expression": node.expression.dump(),
                 "source": node.source.dump()} for node in graph.walk()])
    edits = sqlglot.diff(sqlglot.parse_one("SELECT a FROM x"),
                         sqlglot.parse_one("SELECT a + 1 FROM x"), delta_only=False, f=0.6, t=0.6)
    assert edits
    json.dumps([type(edit).__name__ for edit in edits])
    lineage_api(sqlglot)
