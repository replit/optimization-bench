import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "tasks/sqlite-distinct-plant-facets/environment/workload"
sys.path.insert(0, str(ROOT / "tools"))
import author_sqlite_mirror as author

from judge.verifier import load_task
adapter = load_task(WORKLOAD.parent.parent)


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        packet = self.root / "packet"
        packet.mkdir()
        shutil.copyfile(WORKLOAD / "driver/mirror-schema.sql", packet / "schema.sql")
        rows = [(i, 1, i, json.dumps(["café", "x" * 2048, str(i)]), int(i % 4 == 0))
                for i in range(1, 129)]
        with sqlite3.connect(packet / "input.db") as db:
            db.executescript((packet / "schema.sql").read_text())
            db.execute("INSERT INTO headers VALUES(1,?)", ('["name","body","key"]',))
            db.executemany("INSERT INTO mirror(record_id,source_tag,source_ordinal,record_json,retain) "
                           "VALUES(?,?,?,?,?)", rows)
        db.close()
        shutil.copyfile(packet / "input.db", packet / "before-vacuum.db")
        with sqlite3.connect(packet / "before-vacuum.db") as db:
            db.execute("DELETE FROM mirror WHERE retain=0")
        db.close()
        (packet / "complete-reference.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
        commitment = {
            "files": {name: author.encoding.sha256(packet / name) for name in
                      ("schema.sql", "input.db", "before-vacuum.db", "complete-reference.jsonl")},
            "counts": {"records": 128, "retained": 32},
        }
        (packet / "commitment.json").write_text(json.dumps(commitment))
        self.packet, self.prepared = packet, self.root / "prepared"
        self.cases = author.author(packet, self.prepared, "public")
        self.rows = rows

    def context(self, family):
        row = next(row for row in self.cases if row["family"] == family)
        inputs, scratch = self.root / family / "inputs", self.root / family / "scratch"
        (inputs / "files").mkdir(parents=True)
        scratch.mkdir()
        prepared = (self.prepared / "seed-upsert.db" if family == "batch-upsert"
                    else self.packet / "before-vacuum.db")
        shutil.copyfile(self.packet / "input.db", inputs / "files" / row["sha256"])
        shutil.copyfile(prepared, inputs / "files" / row["prepared_sha256"])
        case = adapter.Case(row["path"], row)
        ctx = SimpleNamespace(inputs=inputs, scratch=scratch, build=self.root / "build",
                              workload=WORKLOAD)
        steps = adapter.prepare(case, ctx)
        self.assertEqual(len(steps), 1)
        self.assertTrue(steps[0].resident)
        return case, ctx

    def complete(self, case, ctx):
        with sqlite3.connect(ctx.scratch / "target.db") as db:
            if case.data["family"] == "batch-upsert":
                db.executemany(
                    "INSERT INTO mirror(record_id,source_tag,source_ordinal,record_json,retain) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET "
                    "source_tag=excluded.source_tag,source_ordinal=excluded.source_ordinal,"
                    "record_json=excluded.record_json,retain=excluded.retain", self.rows)
            else:
                db.execute("VACUUM main")
        db.close()
        (ctx.scratch / "complete.bin").write_bytes(b"SQLALT1\n")

    def collect(self, case, ctx):
        result = SimpleNamespace(exit_code=0, stdout=b"", stderr=b"", leftovers=[],
                                 telemetry={"execution_protocol": "complete"})
        return adapter.collect(case, ctx, [result])[case.id]

    def assert_reference(self, case, value):
        self.assertEqual((len(value), hashlib.sha256(value).hexdigest()),
                         (case.data["expect_len"], case.data["expect_sha"]))
        self.assertEqual(value, gzip.decompress(
            (self.prepared / (case.data["family"] + ".state.jsonl.gz")).read_bytes()))

    def test_complete_outputs_and_preserved_local_notes(self):
        for family in ("batch-upsert", "mirror-vacuum"):
            with self.subTest(family=family):
                case, ctx = self.context(family)
                self.complete(case, ctx)
                self.assert_reference(case, self.collect(case, ctx))

    def test_last_row_same_size_corruption_is_detected(self):
        for family in ("batch-upsert", "mirror-vacuum"):
            with self.subTest(family=family):
                case, ctx = self.context(family)
                self.complete(case, ctx)
                with sqlite3.connect(ctx.scratch / "target.db") as db:
                    db.execute("UPDATE mirror SET record_json=replace(record_json,'x','y') "
                               "WHERE record_id=(SELECT max(record_id) FROM mirror)")
                db.close()
                value = self.collect(case, ctx)
                self.assertEqual(len(value), case.data["expect_len"])
                self.assertNotEqual(hashlib.sha256(value).hexdigest(), case.data["expect_sha"])

    def test_skipped_upsert_does_not_match_complete_reference(self):
        case, ctx = self.context("batch-upsert")
        (ctx.scratch / "complete.bin").write_bytes(b"SQLALT1\n")
        value = self.collect(case, ctx)
        self.assertNotEqual(hashlib.sha256(value).hexdigest(), case.data["expect_sha"])

    def test_skipped_vacuum_is_rejected_even_when_rows_match(self):
        case, ctx = self.context("mirror-vacuum")
        (ctx.scratch / "complete.bin").write_bytes(b"SQLALT1\n")
        with self.assertRaisesRegex(ValueError, "did not compact"):
            self.collect(case, ctx)

    def test_missing_output_after_successful_work_is_rejected(self):
        for family in ("batch-upsert", "mirror-vacuum"):
            with self.subTest(family=family):
                case, ctx = self.context(family)
                self.complete(case, ctx)
                (ctx.scratch / "complete.bin").unlink()
                with self.assertRaisesRegex(ValueError, "Missing complete"):
                    self.collect(case, ctx)

    def test_input_commitment_and_schema_tampering_are_rejected(self):
        case, ctx = self.context("batch-upsert")
        self.complete(case, ctx)
        with sqlite3.connect(ctx.scratch / "target.db") as db:
            db.execute("PRAGMA user_version=3")
        db.close()
        with self.assertRaisesRegex(ValueError, "settings differ"):
            self.collect(case, ctx)
        with (ctx.scratch / "incoming.db").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "lifecycle differs"):
            self.collect(case, ctx)

    def test_each_consumed_artifact_requires_a_commitment(self):
        path = self.packet / "commitment.json"
        original = json.loads(path.read_text())
        for index, name in enumerate(original["files"]):
            with self.subTest(artifact=name):
                changed = dict(original, files={k: v for k, v in original["files"].items()
                                                if k != name})
                path.write_text(json.dumps(changed))
                output = self.root / ("omitted-" + str(index))
                with self.assertRaisesRegex(ValueError, "lacks required commitments"):
                    author.author(self.packet, output, "public")
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
