#!/usr/bin/env python3
"""Read-only complete reference check using the independently built SQLite CLI.

This is untimed operator validation, not an alternative benchmark driver.
Private SQL, rows and reference bytes never appear in its printed summary.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile


class Decoder:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError("Truncated reference")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def integer(self):
        return struct.unpack("<Q", self.take(8))[0]

    def text(self):
        return self.take(self.integer())


def compare(data, requests, expected):
    decoder = Decoder(data)
    if decoder.take(8) != b"FTSFEAS1" or decoder.integer() != len(requests):
        raise ValueError("Reference envelope differs")
    total = 0
    for request, rows in zip(requests, expected, strict=True):
        if tuple(decoder.text() for _ in range(3)) != tuple(request):
            raise ValueError("Request identity differs")
        if decoder.integer() != len(rows) or len(rows) > 20:
            raise ValueError("Complete row count differs")
        for row in rows:
            if (row["id_type"], row["origin_type"], row["title_type"],
                    row["body_type"], row["rank_type"]) != (
                    "integer", "text", "text", "text", "real"):
                raise ValueError("Independent SQL type differs")
            if decoder.integer() != 1 or struct.unpack("<q", decoder.take(8))[0] != row["id"]:
                raise ValueError("Ordered document ID differs")
            for field in ("origin_hex", "title_hex", "body_hex"):
                if decoder.integer() != 3 or decoder.text() != bytes.fromhex(row[field]):
                    raise ValueError("Complete text differs")
            if decoder.integer() != 2 or decoder.take(8) != struct.pack("<d", float(row["rank_text"])):
                raise ValueError("Exact binary64 rank differs")
            total += 1
        if (decoder.integer(), decoder.integer(), decoder.integer()) != (101, 101, 0):
            raise ValueError("Step, extended or finalize status differs")
        if decoder.text() != b"no more rows available":
            raise ValueError("Successful completion message differs")
    if decoder.integer() != 0 or decoder.offset != len(data):
        raise ValueError("Close status or complete framing differs")
    return total


def parse_cli(text, count):
    if not text.startswith("__QUERY_0__\n") or not text.endswith(f"__QUERY_{count}__\n"):
        raise ValueError("Text outside independent query frames")
    expected = []
    for index in range(count):
        start = f"__QUERY_{index}__\n"
        end = f"__QUERY_{index + 1}__\n"
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError("Independent query framing differs")
        body = text.split(start, 1)[1].split(end, 1)[0].strip()
        expected.append(json.loads(body) if body else [])
    return expected


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(packet, sqlite):
    raw_requests = (packet / "requests.tsv").read_bytes()
    lines = raw_requests.split(b"\n")
    requests = [tuple(line.split(b"\t")) for line in lines[:-1]]
    if lines[-1] or not requests or any(len(row) != 3 or not all(row) for row in requests):
        raise ValueError("Request packet shape differs")
    sql = []
    for index, (_, _, match) in enumerate(requests):
        query = match.decode("utf-8").replace("'", "''")
        sql.append(f".print __QUERY_{index}__")
        sql.append(
            "SELECT d.id AS id, typeof(d.id) AS id_type,"
            "hex(CAST(d.origin AS BLOB)) AS origin_hex,typeof(d.origin) AS origin_type,"
            "hex(CAST(d.title AS BLOB)) AS title_hex,typeof(d.title) AS title_type,"
            "hex(CAST(d.body AS BLOB)) AS body_hex,typeof(d.body) AS body_type,"
            "printf('%!.26g',docs_fts.rank) AS rank_text,typeof(docs_fts.rank) AS rank_type "
            "FROM docs AS d JOIN docs_fts ON d.id=docs_fts.rowid "
            f"WHERE docs_fts MATCH '{query}' "
            "ORDER BY docs_fts.rank ASC,d.id ASC LIMIT 20;")
    sql.append(f".print __QUERY_{len(requests)}__")
    database = packet / "input.db"
    before = sha256(database)
    with tempfile.TemporaryDirectory(prefix="sqlite-ranked-oracle-") as temporary:
        env = {"PATH": os.defpath, "HOME": temporary, "LC_ALL": "C.UTF-8", "TZ": "UTC"}
        result = subprocess.run(
            [str(sqlite), "-batch", "-bail", "-readonly", "-json", "-init", "/dev/null",
             database.resolve().as_uri() + "?immutable=1"],
            input=("\n".join(sql) + "\n").encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=1800)
    if result.returncode or result.stderr:
        raise ValueError("Independent SQL execution failed")
    expected = parse_cli(result.stdout.decode("utf-8"), len(requests))
    total = compare((packet / "expected.bin").read_bytes(), requests, expected)
    if sha256(database) != before:
        raise ValueError("Read-only database changed")
    return {"queries": len(requests), "rows": total,
            "complete_rows_types_rank_bits_and_statuses_equal": True,
            "database_unchanged": True, "timing_evidence": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--sqlite", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.packet, args.sqlite)))


if __name__ == "__main__":
    main()
