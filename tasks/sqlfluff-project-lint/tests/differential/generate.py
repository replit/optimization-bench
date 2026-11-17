"""Seeded SQL grammar, malformed/unicode cases and mutable-dialect isolation."""
def generate(rng, budget):
    yield {"kind": "dialect_state", "dialect": "ansi", "marker": "DIFFERENTIAL_SENTINEL"}
    atoms = ["1", "NULL", "'λ🙂'", "a", "COUNT(*)", "'é\\n'"]
    queries = ["", "SELECT", "SELECT 1", "select a,b from t where a=1", "SELECT 'unterminated"]
    import itertools
    matrix = list(itertools.product(["ansi", "postgres", "sqlite"], ["lint", "fix", "parse"]))
    for index in range(max(0, budget - 1)):
        sql = rng.choice(queries) if rng.randrange(3) == 0 else (
            "SELECT " + ", ".join(rng.choices(atoms, k=rng.randint(1, 5))) +
            rng.choice(["", " FROM t", " FROM t WHERE a IS NULL", " UNION ALL SELECT 1"]))
        if index == budget - 2:
            sql = "SELECT '" + "λ" * 16384 + "';"
        dialect, operation = matrix[index % len(matrix)]
        yield {"kind": "sql", "sql": sql, "dialect": dialect, "operation": operation,
               "real_fixture_index": rng.randrange(100) if index % 4 == 0 else None}


def shrink(case):
    if case["kind"] == "dialect_state":
        yield {**case, "marker": "X"}
    elif case.get("sql"):
        for sql in ["", "SELECT 1", case["sql"][:max(1, len(case["sql"]) // 2)]]:
            yield {**case, "sql": sql}
