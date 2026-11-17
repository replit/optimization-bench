"""Only SQLGlot's public parser, transpiler, AST and generator APIs."""
import json
from pathlib import Path
import sys

sys.path.insert(0, "/app/build/runtime")
import sqlglot
from sqlglot import exp


def outcome(operation):
    try:
        return {"value": operation()}
    except Exception as error:
        return {"exception": type(error).__name__, "message": str(error)}


def transpile(case):
    return sqlglot.transpile(
        case["sql"], read=case["read"], write=case["write"],
        pretty=case["pretty"], identify=case["identify"], normalize=case["normalize"],
        comments=case["comments"], error_level=getattr(sqlglot.ErrorLevel, case["error_level"]),
        unsupported_level=getattr(sqlglot.ErrorLevel, case["unsupported_level"]))


def ast_sequence(case):
    node = sqlglot.parse_one(case["sql"], read=case["read"],
                            error_level=getattr(sqlglot.ErrorLevel, case["error_level"]))
    before = {"dump": node.dump(), "sql": node.sql(dialect=case["write"])}
    copied = node.copy()
    identifier = copied.find(exp.Identifier)
    if identifier is not None:
        identifier.set("this", case["marker"])
    changed = copied.sql(dialect=case["write"], pretty=case["pretty"])
    transformed = node.transform(
        lambda item: exp.Literal.number(7) if isinstance(item, exp.Literal) and item.is_number else item,
        copy=True)
    after = {"dump": node.dump(), "sql": node.sql(dialect=case["write"])}
    fresh = sqlglot.parse_one(case["sql"], read=case["read"],
                             error_level=getattr(sqlglot.ErrorLevel, case["error_level"]))
    return {"before": before, "copy_dump": copied.dump(), "copy_sql": changed,
            "transform_dump": transformed.dump(), "transform_sql": transformed.sql(dialect=case["write"]),
            "after": after, "fresh_dump": fresh.dump(), "fresh_sql": fresh.sql(dialect=case["write"])}


def run(case):
    # Repeated A/B/A options and unrelated parser/generator trees in one process.
    other = {**case, "sql": "SELECT 42 AS other_value", "read": "postgres",
             "write": "sqlite", "pretty": not case["pretty"], "identify": not case["identify"]}
    return [{"transpile": outcome(lambda: transpile(current)),
             "ast": outcome(lambda: ast_sequence(current))}
            for current in [case, other, case]]


print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())), ensure_ascii=False, sort_keys=True))
