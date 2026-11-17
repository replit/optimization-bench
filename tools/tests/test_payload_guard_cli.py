"""Exercise the actual standalone image-build entry point with public data only."""
import base64
import hashlib
import gzip
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import payload_check

GUARD = Path(__file__).resolve().parents[1] / "payload_check.py"


class PublicGuardCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        # Real TLS transport and real subprocess CLI; no patched guard functions.
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem"),
                        "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost"],
                       check=True, capture_output=True)
        cls.body = b"original fixture bytes\n"
        cls.sources = {"/raw": cls.body}
        cls.gzip_record = b"original record header\n" + cls.body + b"\r\n"
        cls.sources["/gzip"] = gzip.compress(cls.gzip_record, mtime=0)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            member = tarfile.TarInfo("input")
            member.size = len(cls.body)
            archive.addfile(member, io.BytesIO(cls.body))
        cls.sources["/tar"] = buffer.getvalue()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("input", cls.body)
        cls.sources["/zip"] = buffer.getvalue()
        header = (b"input/".ljust(16) + b"0".ljust(12) + b"0".ljust(6) +
                  b"0".ljust(6) + b"100644".ljust(8) +
                  str(len(cls.body)).encode().ljust(10) + b"`\n")
        cls.sources["/ar"] = b"!<arch>\n" + header + cls.body + (b"\n" if len(cls.body) % 2 else b"")
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        cls.rgb = b"\x12\x34\x56"
        cls.png = (b"\x89PNG\r\n\x1a\n" +
                   chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                   chunk(b"IDAT", zlib.compress(b"\0" + cls.rgb)) + chunk(b"IEND", b""))
        cls.sources["/png"] = cls.png
        parts = payload_check.png_chunks(cls.png)
        cls.recorded = chunk(b"tIME", struct.pack(">HBBBBB", 2025, 1, 2, 3, 4, 5))
        cls.stamped = b"\x89PNG\r\n\x1a\n" + parts[0] + cls.recorded + b"".join(parts[1:])
        cls.sources["/stamped"] = (b"\x89PNG\r\n\x1a\n" + parts[0] +
                                 chunk(b"tIME", struct.pack(">HBBBBB", 2026, 1, 2, 3, 4, 5)) +
                                 b"".join(parts[1:]))
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = cls.sources[self.path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "cert.pem", root / "key.pem")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.base = "https://localhost:" + str(server.server_port)
        cls.environment = {**os.environ, "SSL_CERT_FILE": str(root / "cert.pem"),
                           "NIX_SSL_CERT_FILE": str(root / "cert.pem")}

    def recipes(self):
        def pin(path):
            body = self.sources[path]
            return {"kind": "url", "url": self.base + path, "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest()}
        literal = {"kind": "literal", "base64": base64.b64encode(self.body).decode()}
        result = {
            "url": (pin("/raw"), self.body), "literal": (literal, self.body),
            "zeros": ({"kind": "zeros", "bytes": 4}, bytes(4)),
            "concat": ({"kind": "concat", "parts": [literal, {"kind": "zeros", "bytes": 4}]},
                       self.body + bytes(4)),
        }
        result["gzip-member-slice"] = ({
            "kind": "gzip-member-slice", "archive": pin("/gzip"),
            "compressed_offset": 0, "compressed_bytes": len(self.sources["/gzip"]),
            "compressed_sha256": hashlib.sha256(self.sources["/gzip"]).hexdigest(),
            "record_bytes": len(self.gzip_record),
            "record_sha256": hashlib.sha256(self.gzip_record).hexdigest(),
            "body_offset": len(b"original record header\n"), "body_bytes": len(self.body),
        }, self.body)
        for kind, source in (("tar-member", "/tar"), ("zip-member", "/zip"), ("ar-member", "/ar")):
            result[kind] = ({"kind": kind, "archive": pin(source), "member": "input",
                             "bytes": len(self.body), "sha256": hashlib.sha256(self.body).hexdigest()},
                            self.body)
        result["png-rgb24"] = ({"kind": "png-rgb24", "archive": pin("/png"), "bytes": 3,
                                "sha256": hashlib.sha256(self.rgb).hexdigest()}, self.rgb)
        result["png-recorded-timestamps"] = ({
            "kind": "png-recorded-timestamps",
            "source": {"url": self.base + "/stamped", "bytes": len(self.stamped),
                       "stable_sha256": payload_check.png_stable_sha(self.stamped)},
            "recorded": [{"index": 1, "base64": base64.b64encode(self.recorded).decode()}],
        }, self.stamped)
        return result

    def test_actual_check_and_fetch_cli_for_every_declared_recipe(self):
        self.assertIsNotNone(shutil.which("ffmpeg"), "Real PNG recipe regression requires ffmpeg")
        for kind, (recipe, body) in self.recipes().items():
            for fetch in (False, True):
                with self.subTest(kind=kind, fetch=fetch), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    record = {"scope": "public", "destination": "input.bin", "bytes": len(body),
                              "sha256": hashlib.sha256(body).hexdigest(), "fetch": recipe}
                    (root / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [record]}))
                    if not fetch:
                        (root / "input.bin").write_bytes(body)
                    command = [sys.executable, str(GUARD), *(["--fetch"] if fetch else []), str(root)]
                    run = subprocess.run(command, capture_output=True, text=True, env=self.environment, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual((root / "input.bin").read_bytes(), body)
                    self.assertFalse((root / "payload.json").exists())
                    (root / "input.bin").write_bytes(b"tampered")
                    check = subprocess.run([sys.executable, str(GUARD), str(root)], capture_output=True,
                                           text=True, env=self.environment, timeout=30)
                    self.assertNotEqual(check.returncode, 0)

    def test_all_manifest_recipe_kinds_are_exercised(self):
        declared = set()
        def visit(node):
            if isinstance(node, dict):
                if "kind" in node:
                    declared.add(node["kind"])
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        for path in GUARD.parent.parent.glob("tasks/*/payload.json"):
            for record in json.loads(path.read_text())["files"]:
                if record.get("fetch"):
                    visit(record["fetch"])
        self.assertLessEqual(declared, set(self.recipes()))

    def test_recipe_byte_verification_does_not_allow_unknown_top_level_kinds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = self.body
            (root / "input.bin").write_bytes(body)
            for kind in ("png-rgb24", "url", "invented"):
                record = {"scope": "public", "kind": kind, "destination": "input.bin",
                          "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                (root / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [record]}))
                for args in ([], ["--fetch"]):
                    run = subprocess.run([sys.executable, str(GUARD), *args, str(root)],
                                         capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(run.returncode, 0)

    def test_public_only_file_check_and_fetch_never_require_git_or_private_manifest(self):
        body = b"original pinned fixture bytes\n"
        for fetch in (False, True):
            with self.subTest(fetch=fetch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                record = {"scope": "public", "destination": "input.bin", "bytes": len(body),
                          "sha256": hashlib.sha256(body).hexdigest(),
                          "fetch": {"kind": "literal", "base64": base64.b64encode(body).decode()}}
                (root / "payload-public.json").write_text(json.dumps({"task": "fixture", "files": [record]}))
                if not fetch:
                    (root / "input.bin").write_bytes(body)
                # No Git in PATH, and an invalid override must not be consulted.
                environment = {**os.environ, "PATH": str(root / "empty-path"),
                               "OPTIMBENCH_GIT": str(root / "not-a-git-executable")}
                command = [sys.executable, str(GUARD), *(["--fetch"] if fetch else []), str(root)]
                run = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual((root / "input.bin").read_bytes(), body)
                self.assertFalse((root / "payload.json").exists())
                # A private manifest, even when present and invalid, is not a source.
                (root / "payload.json").write_text("PRIVATE MANIFEST MUST NOT BE READ")
                again = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
                self.assertEqual(again.returncode, 0, again.stderr)
