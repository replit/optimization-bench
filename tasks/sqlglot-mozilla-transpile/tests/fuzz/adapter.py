"""Public-contract generators and exact observations; no candidate verdict policy."""
import hashlib
import json
from pathlib import Path

TASK = "sqlglot-mozilla-transpile"
POLICY = "sqlglot-exact-values-errors-effects-v1"
ROOT = Path(__file__).resolve().parent
SURFACES = (
    "sqlglot.transpile", "sqlglot.parse", "sqlglot.parse_one", "sqlglot.tokenize",
    "sqlglot.expressions.Expression.sql", "sqlglot.expressions.Expression.copy",
    "sqlglot.expressions.Expression.transform", "sqlglot.expressions.Expression.walk",
    "sqlglot.expressions.Expression.find_all", "sqlglot.select",
    "sqlglot.dialects.dialect.Dialect.parse",
    "sqlglot.dialects.dialect.Dialect.generate",
    "sqlglot.parser.Parser.parse", "sqlglot.tokens.Tokenizer.tokenize",
)
GRAMMAR = (
    "", "SELECT", "SELECT (1", "SELECT 'unterminated",
    "SELECT 1 AS a", "SELECT 'λ🙂' AS café, NULL, TRUE",
    "SELECT a + 2 AS x FROM t WHERE a > 1 ORDER BY x",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT a FROM t LEFT JOIN u ON t.id=u.id",
    "SELECT COUNT(*) OVER (PARTITION BY a) FROM t",
    "SELECT ARRAY[1,2,3]", "SELECT 1; SELECT 2;",
    "SELECT /* retained */ a FROM t -- λ\n WHERE a IS NOT NULL",
    "CREATE TABLE t (a INT); INSERT INTO t VALUES (1);",
)
DIALECT_PAIRS = (
    ("bigquery", "duckdb"), ("postgres", "sqlite"), ("mysql", "postgres"),
    ("spark", "snowflake"), ("tsql", "oracle"), ("duckdb", "bigquery"),
)
ALLOWED_OPTIONS = {
    "read", "write", "pretty", "identify", "normalize", "comments",
    "error_level", "unsupported_level",
}


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()


def make_case(surface_id, sql, *, read="bigquery", write="duckdb", index=0):
    return {
        "task": TASK, "surface_id": surface_id,
        "contract_id": "sqlglot-behavior-preservation-v1",
        "precondition_id": "public-sql-or-parser-produced-tree-v1",
        "input": {"origin": "public-sql", "sql": sql},
        "options": {
            "read": read, "write": write, "pretty": bool(index % 2),
            "identify": bool(index % 3), "normalize": bool(index % 4),
            "comments": bool(index % 2), "error_level": "RAISE",
            "unsupported_level": "RAISE",
        },
        "sequence": ["invoke", "interleave", "reinitialize", "invoke"],
        "faults": [], "generator": "sqlglot-public-grammar-v1",
    }


def public_corpus():
    """Read only an explicit public split, never a held-out path or oracle value."""
    corpus = ROOT / "corpus"
    manifest = json.loads((corpus / "manifest.json").read_text())
    if manifest["split"] != "public/train":
        raise ValueError("Only the explicitly frozen public/train corpus is allowed")
    for row in manifest["files"]:
        data = (corpus / row["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Public corpus content hash mismatch")
        yield data.decode("utf-8")


def generate(rng, budget):
    """A bounded case iterable; budget is a count, never an unbounded time loop."""
    if type(budget) is not int or not 0 <= budget <= 100_000:
        raise ValueError("Expected a bounded integer case budget")
    public = tuple(public_corpus())
    for index in range(budget):
        sql = GRAMMAR[index % len(GRAMMAR)]
        generator = "sqlglot-public-grammar-v1"
        if public and index >= len(GRAMMAR) and index % 3 == 0:
            sql = rng.choice(public)
            generator = "sqlglot-public-corpus-v1"
        elif public and index >= len(GRAMMAR) and index % 3 == 1:
            sql = "-- verifier public mutation λ\n" + rng.choice(public) + "\n;"
            generator = "sqlglot-public-corpus-comment-mutation-v1"
        if index and index % 31 == 0:
            sql = "SELECT " + ",".join(f"{j} AS c{j}" for j in range(257))
        read, write = DIALECT_PAIRS[index % len(DIALECT_PAIRS)]
        case = make_case(SURFACES[index % len(SURFACES)], sql,
                         read=read, write=write, index=index)
        case["generator"] = generator
        # Error levels are public choices, including documented error domains.
        case["options"]["error_level"] = ("RAISE", "IMMEDIATE", "WARN", "IGNORE")[index % 4]
        case["options"]["unsupported_level"] = ("RAISE", "WARN", "IGNORE")[index % 3]
        yield case


def precondition(case, surface):
    def reject(reason):
        return {"valid": False, "reason": reason}

    if not isinstance(case, dict) or not isinstance(surface, dict):
        return reject("malformed case/surface")
    if surface.get("visibility") != "public":
        return reject("private or unresolved surface")
    if case.get("task") != TASK or case.get("surface_id") not in SURFACES:
        return reject("surface is not a mapped public entrypoint")
    for key, field in (("surface_id", "id"), ("contract_id", "contract_id"),
                       ("precondition_id", "precondition_id")):
        if case.get(key) != surface.get(field):
            return reject("case/surface identity mismatch")
    value = case.get("input")
    if not isinstance(value, dict) or set(value) != {"origin", "sql"}:
        return reject("only SQL text is admitted; manually constructed ASTs are excluded")
    if value["origin"] != "public-sql" or not isinstance(value["sql"], str):
        return reject("ASTs must originate from documented public parsing")
    try:
        sql_bytes = value["sql"].encode("utf-8")
    except UnicodeEncodeError:
        return reject("SQL must have a valid UTF-8 encoding")
    if len(sql_bytes) > 131_072:
        return reject("SQL exceeds the declared bounded case size")
    options = case.get("options")
    if not isinstance(options, dict) or set(options) != ALLOWED_OPTIONS:
        return reject("unknown or incomplete option family")
    if not all(isinstance(options[k], str) for k in ("read", "write")):
        return reject("dialect names must be strings")
    if not all(type(options[k]) is bool for k in ("pretty", "identify", "normalize", "comments")):
        return reject("boolean option has an invalid type")
    if options["error_level"] not in ("RAISE", "IMMEDIATE", "WARN", "IGNORE"):
        return reject("unknown parser error level")
    if options["unsupported_level"] not in ("RAISE", "WARN", "IGNORE", "IMMEDIATE"):
        return reject("unknown generator error level")
    if case.get("faults") != []:
        return reject("unreviewed I/O or allocator faults are not admitted")
    callback = case.get("callback_fault")
    if callback is not None:
        if (case["surface_id"] not in ("sqlglot.expressions.Expression.transform",
                                      "sqlglot.expressions.Expression.walk")
                or not isinstance(callback, dict)
                or set(callback) != {"errno", "after", "copy"}
                or type(callback["errno"]) is not int or callback["errno"] not in (28, 32)
                or type(callback["after"]) is not int or not 1 <= callback["after"] <= 8
                or type(callback["copy"]) is not bool):
            return reject("unsupported public callback exception schedule")
    if case.get("sequence") not in (["invoke"], ["invoke", "interleave", "reinitialize", "invoke"]):
        return reject("unsupported state sequence")
    return {"valid": True, "reason": "public SQL text; AST operations use only parser-produced trees"}


def shrink(case):
    """Keep the surface/options/sequence/fault schedule unchanged."""
    value = case.get("input", {})
    if value.get("origin") != "public-sql" or not isinstance(value.get("sql"), str):
        return
    sql = value["sql"]
    for smaller in (sql[:len(sql) // 2], "SELECT 1 AS a", ""):
        if len(smaller) < len(sql):
            yield {**case, "input": {**value, "sql": smaller}}


def compare(baseline, candidate, case):
    """Exact contractual content. Messages are NOT normalized or stripped."""
    fields = ("termination", "outcome", "effects")
    for observation in (baseline, candidate):
        if (not isinstance(observation, dict)
                or observation.get("format") != "optimbench-fuzz-observation-v1"
                or not all(isinstance(observation.get(key), dict) for key in fields)
                or observation["termination"].get("kind") not in
                ("return", "exception", "panic", "signal", "timeout")
                or observation["outcome"].get("kind") not in ("ok", "error", "unavailable")):
            raise ValueError("Malformed observation transport; not an equal result")
        if observation["outcome"]["kind"] == "error":
            error = observation["outcome"]
            if (not isinstance(error.get("class"), str) or not error["class"]
                    or not isinstance(error.get("message"), str)):
                raise ValueError("SQLGlot errors require exact class and message fields")
        identity = observation.get("identity", {})
        if (identity.get("task") != TASK
                or identity.get("surface_id") != case["surface_id"]
                or identity.get("case_sha256") != digest(case)):
            raise ValueError("Mismatched observation identity")
    def typed(value):
        # JSON scalar categories must not inherit Python's True == 1 == 1.0.
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)

    differences = [
        {"field": field, "baseline": baseline[field], "candidate": candidate[field]}
        for field in fields if typed(baseline[field]) != typed(candidate[field])
    ]
    return {"equal": not differences, "policy_id": POLICY, "differences": differences}
