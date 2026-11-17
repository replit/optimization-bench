"""Cold public CLI calls; complete bytes, status and final files."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def invoke(directory, flags, stdin=None):
    result = subprocess.run(["/app/build/sqlfmt", *flags], cwd=directory, input=stdin,
                            capture_output=True, timeout=20,
                            env={**os.environ, "HOME": str(directory), "LC_ALL": "C", "TZ": "UTC"})
    return {"exit_code": result.returncode, "stdout_base64": b64(result.stdout),
            # Exactly the protected task's address masking, not error suppression.
            "stderr_base64": b64(re.sub(rb"0x[0-9a-fA-F]{4,}", b"0x_", result.stderr)),
            "files_base64": {p.relative_to(directory).as_posix(): b64(p.read_bytes())
                             for p in sorted(directory.rglob("*")) if p.is_file()}}


def prepare(directory, case):
    directory.mkdir()
    if case["transport"] == "stdin":
        return [], case["sql"].encode("utf-8")
    (directory / "input.sql").write_text(case["sql"], encoding="utf-8")
    if case["transport"] == "file":
        return ["input.sql"], None
    (directory / "nested").mkdir()
    (directory / "nested/other.sql").write_text(case["other"], encoding="utf-8")
    (directory / ".hidden").mkdir()
    (directory / ".hidden/ignored.sql").write_text("SELECT 'unterminated", encoding="utf-8")
    (directory / "ignored.txt").write_text("unformatted non-SQL λ", encoding="utf-8")
    for path in directory.rglob("*"):
        os.utime(path, (0, 0))
    return ["."], None


def run(case):
    result = []
    root = Path("/scratch")
    # A/B/A are independent cold CLI invocations; the CLI exposes no resident API.
    for name, current in [("first", case), ("interleaved", {**case, "flags": ["-l", "-d"]}),
                          ("fresh", case)]:
        directory = root / name
        args, stream = prepare(directory, current)
        result.append(invoke(directory, current["flags"] + args, stream))
        # Reuse the tree to expose rewrite/idempotence behavior after -w.
        result.append(invoke(directory, current["flags"] + args, stream))
    special = root / "special"
    special.mkdir()
    result.append(invoke(special, [case["special"]], b""))
    return result


print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())), ensure_ascii=False, sort_keys=True))
