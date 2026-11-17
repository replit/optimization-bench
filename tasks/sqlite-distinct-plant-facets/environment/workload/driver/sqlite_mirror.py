"""Protected full logical-state encoding, independent of the candidate SQLite."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield db
    finally:
        db.close()


def metadata(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Database integrity differs")
    settings = [db.execute("PRAGMA " + name).fetchone()[0] for name in
                ("page_size", "auto_vacuum", "user_version", "journal_mode")]
    if settings != [4096, 0, 2, "delete"]:
        raise ValueError("Fixed database settings differ")
    return [
        ["format", "sqlite-mirror-state-v1"],
        ["settings", settings],
        ["schema", db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall()],
        ["headers", db.execute(
            "SELECT source_tag,fields_json FROM headers ORDER BY source_tag").fetchall()],
    ]


def encode(stream, prefix, rows, maximum=None):
    """Every field/type is serialized, not a sampled digest or row-count sketch."""
    size = count = 0

    def emit(value):
        nonlocal size
        raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        size += len(raw)
        if maximum is not None and size > maximum:
            raise ValueError("Complete output exceeds its commitment")
        stream.write(raw)

    for value in prefix:
        emit(value)
    for row in rows:
        if (len(row) != 6 or [type(value) for value in row] !=
                [int, int, int, str, int, str]):
            raise ValueError("Record field types differ")
        emit(row)
        count += 1
    emit(["complete_rows", count])
    return size, count


def collect_state(target, output, family, before_bytes, maximum):
    with readonly(target) as db:
        prefix = metadata(db)
        if family == "mirror-vacuum":
            if (db.execute("PRAGMA freelist_count").fetchone()[0] != 0
                    or target.stat().st_size >= before_bytes):
                raise ValueError("VACUUM did not compact the retained database")
            prefix.append(["compacted", True])
        rows = db.execute(
            "SELECT record_id,source_tag,source_ordinal,record_json,retain,local_note "
            "FROM mirror ORDER BY record_id")
        with output.open("xb") as stream:
            encode(stream, prefix, rows, maximum)
    return output.read_bytes()
