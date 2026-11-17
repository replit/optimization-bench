import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "ranked_oracle", Path(__file__).parents[1] / "verify_sqlite_ranked.py")
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)
REQUEST = (b"request", b"question?", b'"term"')


def integer(value):
    return struct.pack("<Q", value)


def text(value):
    return integer(len(value)) + value


def row(identifier=1):
    return {"id": identifier, "id_type": "integer", "origin_type": "text",
            "title_type": "text", "body_type": "text", "rank_type": "real",
            "origin_hex": b"source".hex(), "title_hex": "é".encode().hex(),
            "body_hex": b"whole\x00body".hex(), "rank_text": "-0.125"}


def frame(rows):
    result = b"FTSFEAS1" + integer(1) + b"".join(map(text, REQUEST)) + integer(len(rows))
    for item in rows:
        result += integer(1) + struct.pack("<q", item["id"])
        for field in ("origin_hex", "title_hex", "body_hex"):
            result += integer(3) + text(bytes.fromhex(item[field]))
        result += integer(2) + struct.pack("<d", float(item["rank_text"]))
    return (result + integer(101) + integer(101) + integer(0)
            + text(b"no more rows available") + integer(0))


class CompleteReferenceTests(unittest.TestCase):
    def test_complete_fields_empty_results_and_tied_order(self):
        for rows in ([], [row()], [row(1), row(2)]):
            self.assertEqual(ORACLE.compare(frame(rows), [REQUEST], [rows]), len(rows))
        with self.assertRaises(ValueError):
            ORACLE.compare(frame([row(2), row(1)]), [REQUEST], [[row(1), row(2)]])

    def test_each_full_field_rank_bit_and_sql_type_are_required(self):
        original = row()
        changes = {"id": 2, "origin_hex": b"other".hex(), "title_hex": b"other".hex(),
                   "body_hex": b"whole".hex(), "rank_text": "-0.12500000000000003",
                   "rank_type": "text", "body_type": "null"}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                ORACLE.compare(frame([original]), [REQUEST], [[{**original, field: value}]])
        with self.assertRaises(ValueError):
            ORACLE.compare(frame([original]), [(b"other", *REQUEST[1:])], [[original]])

    def test_binary_types_statuses_close_and_complete_consumption(self):
        rows = [row()]
        valid = frame(rows)
        row_start = 16 + sum(8 + len(value) for value in REQUEST) + 8
        suffix_size = 24 + 8 + len(b"no more rows available") + 8
        status_start = len(valid) - suffix_size
        mutations = [valid[:-1], valid + b"trailing", b"BADMAGIC" + valid[8:]]
        for offset in (row_start, status_start, status_start + 8, status_start + 16, len(valid) - 8):
            mutations.append(valid[:offset] + integer(99) + valid[offset + 8:])
        mutations.append(valid.replace(b"no more rows available", b"unexpected completion"))
        for mutated in mutations:
            with self.subTest(length=len(mutated)), self.assertRaises(ValueError):
                ORACLE.compare(mutated, [REQUEST], [rows])

    def test_cli_frames_require_exact_boundaries(self):
        valid = "__QUERY_0__\n[]\n__QUERY_1__\n"
        self.assertEqual(ORACLE.parse_cli(valid, 1), [[]])
        self.assertEqual(ORACLE.parse_cli("__QUERY_0__\n__QUERY_1__\n", 1), [[]])
        for invalid in ("noise\n" + valid, valid + "noise\n",
                        valid.replace("__QUERY_1__", "__QUERY_0__"),
                        valid.replace("[]", "{broken}")):
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                ORACLE.parse_cli(invalid, 1)

    def test_cli_failures_and_blank_request_lines_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            packet = Path(directory)
            request_path = packet / "requests.tsv"
            request_path.write_bytes(b"\t".join(REQUEST) + b"\n")
            (packet / "input.db").write_bytes(b"synthetic fixture")
            for returncode, stderr in ((1, b""), (0, b"error")):
                with patch.object(ORACLE.subprocess, "run", return_value=SimpleNamespace(
                        returncode=returncode, stderr=stderr, stdout=b"")):
                    with self.assertRaisesRegex(ValueError, "SQL execution failed"):
                        ORACLE.verify(packet, Path("/unused"))
            request_path.write_bytes(b"\t".join(REQUEST) + b"\n\n")
            with self.assertRaisesRegex(ValueError, "packet shape"):
                ORACLE.verify(packet, Path("/unused"))


if __name__ == "__main__":
    unittest.main()
