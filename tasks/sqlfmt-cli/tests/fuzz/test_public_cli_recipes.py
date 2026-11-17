import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from tools.payload_check import Fetcher


PATH = Path(__file__).with_name("public_cli_recipes.py")
SPEC = importlib.util.spec_from_file_location("sqlfmt_public_recipes", PATH)
RECIPES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECIPES)


class PublicCLIRecipeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = {"fixture": {"url": "https://github.com/example/sql",
                                    "commit": "a" * 40}}
        self.bodies = {}

    def member(self, path, raw):
        digest = hashlib.sha256(raw).hexdigest()
        destination = self.root / digest
        destination.write_bytes(raw)
        self.bodies[digest] = destination
        return {"repo": "fixture", "path": path, "sha256": digest}

    def convert(self, body, transport, members):
        raw = b"CLI1\nsqlfmt\n" + transport + b"\n-\n" + body
        document = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                    "members": members}
        recipe = RECIPES.recipe(raw, document, self.sources)
        fetcher = Fetcher(self.root / "cache")
        with mock.patch.object(fetcher, "url", side_effect=lambda row: self.bodies[row["sha256"]]):
            self.assertEqual(fetcher.render(recipe), raw)
        return recipe

    def test_single_file_sql_is_fetched_not_embedded_as_literal(self):
        body = b"select 'separate upstream body';\n"
        recipe = self.convert(body, b"stdin", [self.member("test.sql", body)])
        self.assertEqual([part["kind"] for part in recipe["parts"]], ["literal", "url"])
        self.assertTrue(recipe["parts"][1]["url"].endswith("/" + "a" * 40 + "/test.sql"))

    def test_tar_headers_pax_metadata_and_padding_round_trip_exactly(self):
        stream, members = io.BytesIO(), []
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path, body in [("short.sql", b"select 1;\n"),
                               ("long/" + "x" * 120 + ".sql", b"select 'unicode \xce\xbb';\n")]:
                info = tarfile.TarInfo("fixture/" + path)
                info.size, info.mode = len(body), 0o644
                archive.addfile(info, io.BytesIO(body))
                members.append(self.member(path, body))
        recipe = self.convert(stream.getvalue(), b"tar", members)
        self.assertEqual(sum(part["kind"] == "url" for part in recipe["parts"]), 2)

    def test_outer_input_commitment_cannot_be_reauthored(self):
        with self.assertRaisesRegex(ValueError, "commitment"):
            RECIPES.recipe(b"wrong", {"size": 5, "sha256": "0" * 64}, self.sources)

    def test_fixture_staging_rejects_wrong_original_bytes_before_writing(self):
        digest = "f783e5a7c220b5cd5415914dfe6a511d6b9ac1326083a6751afa334f147acb6e"
        destination = f"environment/workload/inputs/files/{digest}"
        (self.root / "payload.json").write_text(json.dumps({"files": [{
            "scope": "public", "destination": destination, "sha256": digest,
            "bytes": 73197, "fetch": {"kind": "concat", "parts": []}}]}))
        with mock.patch.object(RECIPES, "TASK", self.root), mock.patch(
                "tools.payload_check.Fetcher") as fetcher:
            fetcher.return_value.render.return_value = b"wrong"
            with self.assertRaisesRegex(ValueError, "commitment"):
                RECIPES.stage_cli_fixture()
        self.assertFalse((self.root / destination).exists())


if __name__ == "__main__":
    unittest.main()
