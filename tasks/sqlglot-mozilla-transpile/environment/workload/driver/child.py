"""One whole-process SQLGlot workload; every query uses the same imported package."""
import json
from pathlib import Path
import sys

import sqlglot

root = Path(sys.argv[2])
rows = json.loads((root / "cases.json").read_text())
for row in rows:
    raw = (root / row["sha256"]).read_bytes()
    text = raw.decode("utf-8")
    try:
        result = sqlglot.transpile(
            text, read="bigquery", write="duckdb", pretty=False,
            error_level=sqlglot.ErrorLevel.RAISE,
            unsupported_level=sqlglot.ErrorLevel.RAISE)
        if type(result) is not list or any(type(value) is not str for value in result):
            raise TypeError("transpile must return a list of strings")
        data = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception as error:
        data = f"{type(error).__name__}: {error}".encode("utf-8")
    (root / (row["path"] + ".out")).write_bytes(data)
