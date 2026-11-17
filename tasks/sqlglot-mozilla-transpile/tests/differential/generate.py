"""Seeded original Mozilla queries plus grammar/mode mutations."""
import json
from pathlib import Path


def generate(rng, budget):
    root = Path(__file__).parents[2] / "environment/workload/inputs"
    manifest = json.loads((root / "manifest.json").read_text())
    # Seeds come only from inputs stored in the repository; the declared
    # fetch-only (TPC-derived) inputs are absent until preflight downloads them.
    packet = json.loads((root.parents[1] / "public-input-packet.json").read_text())
    fetched = {d["sha256"] for d in packet["derived_inputs"]}
    docs = [d for batches in manifest["splits"].values() for batch in batches
            for d in batch["docs"] if d["bytes"] <= 16000 and d["sha256"] not in fetched]
    real = [(root / "files" / d["sha256"]).read_text() for d in docs]
    if not real:
        raise ValueError("No frozen real SQL inputs")
    dialects = [("bigquery", "duckdb"), ("duckdb", "bigquery"),
                ("postgres", "sqlite"), ("mysql", "postgres"),
                ("spark", "snowflake"), ("tsql", "oracle")]
    grammar = [
        "", "SELECT", "SELECT 'unterminated", "SELECT (1",
        "SELECT 'λ🙂' AS café, NULL, 1 + 2 AS n; SELECT 2;",
        "WITH x AS (SELECT 1 AS a) SELECT a, ROW_NUMBER() OVER (ORDER BY a) FROM x",
        "SELECT ARRAY[1,2,3]", "SELECT a FROM t LEFT JOIN u ON t.id=u.id WHERE a IS NOT NULL",
        "SELECT x::INTEGER FROM t UNION ALL SELECT 2",
        "SELECT JSON_EXTRACT('{\"x\": 1}', '$.x')",
        "-- Unicode λ🙂\nSELECT `a`, COUNT(*) FROM `table` GROUP BY 1",
        "CREATE TABLE t (a INT, b VARCHAR(20)); INSERT INTO t VALUES (1,'x');",
    ]
    for index in range(budget):
        sql = grammar[index % len(grammar)]
        if index % 3 == 0:
            sql = rng.choice(real) + "\n;\n" + sql
        elif index % 3 == 1 and index >= 12:
            sql = "-- seeded λ🙂 original query\n" + rng.choice(real)
        if index == budget - 1:
            sql = "SELECT " + ",".join(f"{i} AS c{i}" for i in range(256))
        read, write = dialects[index % len(dialects)]
        yield {"sql": sql, "read": read, "write": write,
               "pretty": index % 2 == 0, "identify": index % 3 == 0,
               "normalize": index % 4 == 0, "comments": index % 2 == 1,
               "error_level": ["RAISE", "IMMEDIATE", "WARN", "IGNORE"][index // 6 % 4],
               "unsupported_level": ["RAISE", "IGNORE", "WARN"][index % 3],
               "marker": "renamed_" + str(rng.randrange(10000))}


def shrink(case):
    for sql in [case["sql"][:len(case["sql"]) // 2], "SELECT 'λ' AS a", "SELECT 1 AS a", ""]:
        yield {**case, "sql": sql, "marker": "x"}
