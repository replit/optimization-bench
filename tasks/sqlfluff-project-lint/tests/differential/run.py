import concurrent.futures
import json
from pathlib import Path
import sys

sys.path.insert(0, "/app/build/runtime")
import sqlfluff
from sqlfluff.core.dialects import dialect_selector


def run(case):
    if case["kind"] == "dialect_state":
        first = dialect_selector(case["dialect"])
        before = sorted(first.sets("reserved_keywords"))
        first.sets("reserved_keywords").add(case["marker"])
        second = dialect_selector(case["dialect"])
        other = dialect_selector("postgres")
        repeated = [case["marker"] in dialect_selector(case["dialect"]).sets("reserved_keywords") for _ in range(3)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            independent = list(pool.map(lambda _: case["marker"] in dialect_selector(case["dialect"]).sets("reserved_keywords"), range(2)))
        return {"before": before, "second": sorted(second.sets("reserved_keywords")),
                "other_leaked": case["marker"] in other.sets("reserved_keywords"),
                "repeated": repeated, "independent": independent}
    try:
        sql = case["sql"]
        if case.get("real_fixture_index") is not None:
            files = sorted(Path("/app/sqlfluff/test/fixtures/dialects").glob("**/*.sql"))
            if files:
                raw = files[case["real_fixture_index"] % len(files)].read_text(errors="replace")[:4000]
                sql = "-- seeded recombination\n" + raw + "\n" + sql
        result = getattr(sqlfluff, case["operation"])(sql, dialect=case["dialect"])
        return {"value": result}
    except Exception as error:
        return {"exception": type(error).__name__, "message": str(error)}


print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True, ensure_ascii=False))
