import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from image_layers import audit_archive


class ImageLayerTests(unittest.TestCase):
    body = b"original frozen SQL input;\n"
    canary = b"private-image-input-canary"

    def image(self, root, layers, config=b"{}"):
        def add(archive, name, data):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

        path = root / "image.tar"
        names = []
        with tarfile.open(path, "w") as image:
            for index, files in enumerate(layers):
                name = f"{index}/layer.tar.gz"
                names.append(name)
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:gz") as layer:
                    for filename, data in files.items():
                        add(layer, filename, data)
                add(image, name, buffer.getvalue())
            add(image, "config.json", config)
            add(image, "manifest.json", json.dumps([{
                "Config": "config.json", "RepoTags": [], "Layers": names}]).encode())
        return path

    def audit(self, layers, config=b"{}"):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.image(Path(temporary), layers, config)
            return audit_archive(path, {hashlib.sha256(self.body).hexdigest(): len(self.body)},
                                 self.canary)

    def test_clean_layers_pass(self):
        result = self.audit([{"app/code": b"protected code"}, {"app/metadata": b"{}"}])
        self.assertTrue(result["passed"])
        self.assertEqual(result["layers"], 2)

    def test_late_copy_then_delete_still_rejects_original_body(self):
        result = self.audit([{"app/code": b"protected code"},
                             {"tmp/body": self.body}, {"tmp/.wh.body": b""}])
        self.assertFalse(result["passed"])
        self.assertEqual(result["hits"][0]["kind"], "known-public-input")
        self.assertEqual(result["hits"][0]["layer"], "1/layer.tar.gz")

    def test_embedded_canary_survives_later_deletion(self):
        result = self.audit([{"tmp/probe": b"prefix-" + self.canary + b"-suffix"},
                             {"tmp/.wh.probe": b""}])
        self.assertFalse(result["passed"])
        self.assertEqual(result["hits"][0]["kind"], "audit-canary")

    def test_image_metadata_is_scanned(self):
        result = self.audit([{"app/code": b"protected code"}], self.canary)
        self.assertFalse(result["passed"])
        self.assertEqual(result["hits"][0]["metadata"], "config.json")

    def test_no_layers_is_invalid_not_a_pass(self):
        with self.assertRaisesRegex(ValueError, "no layers"):
            self.audit([])
