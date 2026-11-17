"""One native batch per family, linked to the same rebuilt SQLite archive."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
from runner import Case, Step, documents, contained

_spec = importlib.util.spec_from_file_location(
    "sqlite_mirror_state", Path(__file__).parent / "driver/sqlite_mirror.py")
_mirror = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mirror)
collect_state, sha256 = _mirror.collect_state, _mirror.sha256

ENV = {"OMP_NUM_THREADS": "8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
references = documents


def cases(inputs):
    return [Case(doc["path"], doc) for doc in documents(inputs)]


def prepare(case, ctx):
    row = case.data
    family = row["family"]
    if family in ("batch-upsert", "mirror-vacuum"):
        for prefix, name in (("", "incoming.db"), ("prepared_", "target.db")):
            source = contained(ctx.inputs / "files", row[prefix + "sha256"])
            if (source.stat().st_size != row[prefix + "bytes"]
                    or sha256(source) != row[prefix + "sha256"]):
                raise ValueError("Alternate input commitment changed")
            shutil.copyfile(source, ctx.scratch / name)
        mode = "upsert" if family == "batch-upsert" else "vacuum"
        return [Step([
            str(ctx.build / "bulk"), mode, str(ctx.scratch / "incoming.db"),
            str(ctx.scratch / "target.db"), str(ctx.workload / "driver/mirror-schema.sql"),
            str(ctx.scratch / "complete.bin"),
        ], resident=True, timeout=1800)]
    if family not in ("country-filtered-facets", "ranked-documents"):
        raise ValueError("Unknown workload family")
    requests = contained(ctx.inputs / "files", row["requests_sha256"])
    if (requests.stat().st_size != row["requests_bytes"] or
            hashlib.sha256(requests.read_bytes()).hexdigest() != row["requests_sha256"]):
        raise ValueError("Request commitment changed")
    database = contained(ctx.inputs / "files", row["sha256"])
    if (database.stat().st_size != row["bytes"]
            or hashlib.sha256(database.read_bytes()).hexdigest() != row["sha256"]):
        raise ValueError("Database commitment differs")
    shutil.copyfile(database, ctx.scratch / "input.db")
    shutil.copyfile(requests, ctx.scratch / "requests.tsv")
    command = ([str(ctx.build / "facets"), str(ctx.scratch / "input.db"),
                str(ctx.scratch / "requests.tsv"), str(ctx.scratch / "result.bin"),
                str(row["requests"])] if family == "country-filtered-facets" else
               [str(ctx.build / "search"), str(ctx.scratch / "input.db"),
                str(ctx.scratch / "requests.tsv"), str(ctx.scratch / "result.bin"),
                "resident"])
    return [Step(command, resident=True, timeout=1800)]


def collect(case, ctx, results):
    if (len(results) != 1 or results[0].exit_code != 0 or
            results[0].stdout or results[0].stderr):
        raise ValueError("Native workload failed")
    if (results[0].telemetry.get("execution_protocol") != "complete"
            or results[0].leftovers):
        raise ValueError("Native lifecycle incomplete")
    if case.data["family"] in ("batch-upsert", "mirror-vacuum"):
        marker = ctx.scratch / "complete.bin"
        target = ctx.scratch / "target.db"
        incoming = ctx.scratch / "incoming.db"
        if (not marker.is_file() or marker.is_symlink() or marker.stat().st_size != 8
                or marker.read_bytes() != b"SQLALT1\n"):
            raise ValueError("Missing complete alternate output")
        if (not target.is_file() or target.is_symlink()
                or not incoming.is_file() or incoming.is_symlink()
                or sha256(incoming) != case.data["sha256"]
                or list(ctx.scratch.glob("*.db-*"))):
            raise ValueError("Alternate database lifecycle differs")
        return {case.id: collect_state(
            target, ctx.scratch / "result.bin", case.data["family"],
            case.data["prepared_bytes"], case.data["expect_len"])}
    database = ctx.scratch / "input.db"
    if (hashlib.sha256(database.read_bytes()).hexdigest() != case.data["sha256"]
            or list(ctx.scratch.glob("input.db-*"))):
        raise ValueError("Read-only database state changed")
    output = ctx.scratch / "result.bin"
    if not output.is_file() or output.is_symlink():
        raise ValueError("Complete regular result file required")
    return {case.id: output.read_bytes()}

# ---- Suite workloads (appended; the family-dispatched code above is unchanged) ----
# Each suite workload is one case per split, selected by the manifest "workload" key.
import sqlite3  # noqa: E402  (independent pristine reader for exported database images)
import struct  # noqa: E402
from runner import bundle  # noqa: E402

# workload -> (driver binary, argv layout, database images exported by the driver)
QUERY = "db-requests-out-count"
WORKLOADS = {
    "integrity-check": ("ichk", "out-then-files", 0),
    "tatoeba-create-index": ("cidx", "prefix-files", None),
    "usgs-geojson-json": ("usgs", QUERY, 0),
    "storms-printf-report": ("storms", QUERY, 0),
    "geonames-rtree": ("rtree", QUERY, 0),
    "gtfs-departure-join": ("gtfs", QUERY, 0),
    "ghcn-window-series": ("ghcn", QUERY, 0),
    "decimal-ledger": ("dec", QUERY, 0),
    "percentile": ("pct", QUERY, 0),
    "fkey-cascade": ("fkc", QUERY, 1),
}
# Sorter spill files of the CREATE INDEX workload live on the container's /tmp tmpfs
# (fixed protected setting; the runner's per-step TMPDIR is on scratch storage).
STEP_ENV = {"tatoeba-create-index": {"SQLITE_TMPDIR": "/tmp"}}


def _authentic_copy(source, entry, target):
    if source.stat().st_size != entry["bytes"]:
        raise ValueError("Input size commitment changed: " + entry["name"])
    digest = hashlib.sha256()
    with source.open("rb") as stream, target.open("wb") as copy:
        while chunk := stream.read(1 << 24):
            digest.update(chunk)
            copy.write(chunk)
    if digest.hexdigest() != entry["sha256"]:
        raise ValueError("Input commitment changed: " + entry["name"])


def prepare_suite(case, ctx):
    row = case.data
    binary, layout, _ = WORKLOADS[row["workload"]]
    staged = []
    for index, entry in enumerate(row["files"]):
        target = ctx.scratch / f"in-{index}-{entry['name']}"
        _authentic_copy(contained(ctx.inputs / "files", entry["sha256"]), entry, target)
        staged.append(str(target))
    out = str(ctx.scratch / "result")
    exe = str(ctx.build / binary)
    if layout == QUERY:
        argv = [exe, staged[0], staged[1], out + ".bin", str(row["requests"])]
    elif layout == "out-then-files":
        argv = [exe, out + ".bin", *staged]
    else:
        argv = [exe, out, *staged]
    return [Step(argv, env=STEP_ENV.get(row["workload"], {}), resident=True, timeout=1800)]


def collect_suite(case, ctx, results):
    row = case.data
    if (len(results) != 1 or results[0].exit_code != 0 or
            results[0].stdout or results[0].stderr):
        raise ValueError("Suite workload operation failed: " + row["workload"])
    if (results[0].telemetry.get("execution_protocol") != "complete"
            or results[0].leftovers):
        raise ValueError("Native lifecycle incomplete")
    _, layout, images = WORKLOADS[row["workload"]]
    files = {"result.bin": (ctx.scratch / "result.bin").read_bytes()}
    if images is None:  # one exported image per input document
        images = len(row["files"])
    for index in range(images):
        name = f"result.{index}.db" if layout != QUERY else "result.bin.db"
        files[f"image-{index}"] = image_commitment(ctx.scratch / name)
    return {case.id: bundle(files) if len(files) > 1 else files["result.bin"]}


def image_commitment(path):
    """Independent pristine reader (CPython sqlite3, never the candidate library):
    integrity_check rows plus a canonical logical dump digest. Page layout is not
    part of the contract; schema text, header pragmas and every row are."""
    if not path.is_file() or path.is_symlink():
        return b"missing-image\n"
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        integrity = [r[0] for r in con.execute("PRAGMA integrity_check")]
        digest, size = hashlib.sha256(), 0

        def put(data):
            nonlocal size
            digest.update(data)
            size += len(data)

        def val(v):
            if v is None:
                put(b"N")
            elif isinstance(v, int):
                put(b"I" + str(v).encode() + b";")
            elif isinstance(v, float):
                put(b"R" + v.hex().encode() + b";")
            elif isinstance(v, str):
                e = v.encode("utf-8", "surrogatepass")
                put(b"T" + struct.pack("<Q", len(e)) + e)
            else:
                put(b"B" + struct.pack("<Q", len(v)) + bytes(v))
        schema = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name").fetchall()
        for item in schema:
            for v in item:
                val(v)
        for pragma in ("page_size", "user_version", "application_id", "encoding", "auto_vacuum"):
            val(con.execute("PRAGMA " + pragma).fetchone()[0])
        rows = 0
        for kind, name, _, sql in schema:
            if kind != "table" or (sql or "").upper().startswith("CREATE VIRTUAL TABLE"):
                continue
            quoted = '"%s"' % name.replace('"', '""')
            put(b"TABLE:" + name.encode())
            if sql and "WITHOUT ROWID" in sql.upper():
                keys = [r[1] for r in sorted(con.execute("PRAGMA table_info(%s)" % quoted), key=lambda r: r[5]) if r[5]]
                cursor = con.execute("SELECT * FROM %s ORDER BY %s" % (quoted, ",".join('"%s"' % k for k in keys)))
            else:
                cursor = con.execute("SELECT rowid, * FROM %s ORDER BY rowid" % quoted)
            for item in cursor:
                rows += 1
                for v in item:
                    val(v)
    except sqlite3.DatabaseError as error:
        return b"unreadable-image:" + str(error).encode() + b"\n"
    finally:
        con.close()
    return ("integrity=%s\nrows=%d\ndump_bytes=%d\ndump_sha256=%s\n" % (
        "|".join(integrity), rows, size, digest.hexdigest())).encode()

_family_prepare, _family_collect = prepare, collect


def prepare(case, ctx):  # noqa: F811
    return prepare_suite(case, ctx) if "workload" in case.data else _family_prepare(case, ctx)


def collect(case, ctx, results):  # noqa: F811
    return collect_suite(case, ctx, results) if "workload" in case.data else _family_collect(case, ctx, results)
