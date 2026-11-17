#!/usr/bin/env python3
"""Package an already-authenticated eligibility packet without new source data."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tasks/sqlite-distinct-plant-facets/environment/workload/driver/sqlite_mirror.py"
spec = importlib.util.spec_from_file_location("mirror_encoding", HELPER)
encoding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(encoding)


class DigestWriter:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def write(self, raw):
        self.digest.update(raw)
        return self.stream.write(raw)


def author(packet, output, split):
    commitment = json.loads((packet / "commitment.json").read_text())
    required = {"schema.sql", "input.db", "before-vacuum.db", "complete-reference.jsonl"}
    if not required.issubset(commitment["files"]):
        raise ValueError("Eligibility packet lacks required commitments")
    for name, digest in commitment["files"].items():
        path = packet / name
        if not path.resolve().is_relative_to(packet.resolve()):
            raise ValueError("Packet path escapes its directory")
        if encoding.sha256(path) != digest:
            raise ValueError("Eligibility packet commitment differs")
    output.mkdir(parents=True, exist_ok=False)
    seed = output / "seed-upsert.db"
    with sqlite3.connect(seed) as db:
        db.executescript((packet / "schema.sql").read_text())
        db.execute("ATTACH DATABASE ? AS incoming", (str(packet / "input.db"),))
        db.execute("INSERT INTO headers SELECT * FROM incoming.headers")
        db.execute("INSERT INTO mirror SELECT * FROM incoming.mirror WHERE record_id%2=1")
        db.execute("UPDATE mirror SET local_note='retained local note' WHERE record_id=1")
    db.close()
    source_hash = encoding.sha256(packet / "input.db")
    cases = []
    for family in ("batch-upsert", "mirror-vacuum"):
        prepared = seed if family == "batch-upsert" else packet / "before-vacuum.db"
        with encoding.readonly(packet / "input.db") as source:
            prefix = encoding.metadata(source)
            if family == "mirror-vacuum":
                prefix.append(["compacted", True])

            def reference_rows():
                original = iter(source.execute(
                    "SELECT record_id,source_tag,source_ordinal,record_json,retain,local_note "
                    "FROM mirror ORDER BY record_id"))
                total = 0
                with (packet / "complete-reference.jsonl").open(encoding="utf-8") as reference:
                    for line in reference:
                        values = json.loads(line)
                        before = values + ["local-only"]
                        observed = next(original, None)
                        if (observed is None or list(observed) != before
                                or [type(v) for v in observed] != [type(v) for v in before]):
                            raise ValueError("Full independent source reference differs")
                        total += 1
                        if family == "mirror-vacuum" and not values[4]:
                            continue
                        note = ("retained local note" if family == "batch-upsert"
                                and values[0] == 1 else "local-only")
                        yield values + [note]
                if next(original, None) is not None or total != commitment["counts"]["records"]:
                    raise ValueError("Source record population differs")

            reference_path = output / (family + ".state.jsonl.gz")
            with reference_path.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=1) as compressed:
                    writer = DigestWriter(compressed)
                    length, count = encoding.encode(writer, prefix, reference_rows())
            expected_count = commitment["counts"]["records" if family == "batch-upsert" else "retained"]
            if count != expected_count:
                raise ValueError("Complete output population differs")
        cases.append({
            "family": family, "path": family + "-" + split,
            "sha256": source_hash, "bytes": (packet / "input.db").stat().st_size,
            "prepared_sha256": encoding.sha256(prepared), "prepared_bytes": prepared.stat().st_size,
            "requests": 1, "records": count,
            "expect_len": length, "expect_sha": writer.digest.hexdigest(),
        })
    (output / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    shutil.copyfile(packet / "commitment.json", output / "eligibility-commitment.json")
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("public", "heldout"), required=True)
    args = parser.parse_args()
    cases = author(args.packet, args.output, args.split)
    print(json.dumps({"split": args.split, "families": len(cases),
                      "complete_reference_fields": True}))
