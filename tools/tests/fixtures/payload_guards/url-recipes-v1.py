#!/usr/bin/env python3
"""Standalone public-only Docker build guard; identical in every environment."""
import hashlib
import json
from pathlib import Path
import sys
import base64
import io
import os
import shutil
import tarfile
import tempfile
import urllib.request
import threading
import struct
import zlib
import concurrent.futures
import zipfile
import subprocess


def png_chunks(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected PNG signature")
    chunks, offset = [], 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        raw = data[offset:offset + size + 12]
        if len(raw) != size + 12 or zlib.crc32(raw[4:-4]) != struct.unpack(">I", raw[-4:])[0]:
            raise ValueError("PNG chunk integrity mismatch")
        chunks.append(raw)
        offset += size + 12
    return chunks


def png_time_chunk(raw):
    return (raw[4:8] == b"tIME" or
            raw[4:8] == b"tEXt" and raw[8:-4].split(b"\0", 1)[0] in
            (b"date:create", b"date:modify", b"date:timestamp"))


def png_stable_sha(data):
    return hashlib.sha256(b"\x89PNG\r\n\x1a\n" +
                          b"".join(raw for raw in png_chunks(data) if not png_time_chunk(raw))).hexdigest()


def verified(path, record):
    if not path.is_file() or path.stat().st_size != record["bytes"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]


class Fetcher:
    """Build-time public acquisition only; every transport and final result is pinned."""
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.locks = {}
        self.lock_guard = threading.Lock()
        self.checked = {}

    def url(self, record):
        with self.lock_guard:
            lock = self.locks.setdefault(record["url"], threading.Lock())
        with lock:
            return self._url(record)

    def _url(self, record):
        url = record["url"]
        if not url.startswith("https://") or "@" in url.split("/")[2]:
            raise ValueError("Expected a credential-free public HTTPS source")
        target = self.cache / hashlib.sha256(url.encode()).hexdigest()
        identity = (record["bytes"], record["sha256"])
        if target.is_file():
            stat = target.stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if self.checked.get(url) == (identity, stamp):
                return target
        if verified(target, record):
            stat = target.stat()
            self.checked[url] = (identity, (stat.st_ino, stat.st_size, stat.st_mtime_ns))
            return target
        target.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "optimbench-public-inputs/1"})
            with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as out:
                remaining = record["bytes"] + 1
                while remaining:
                    chunk = response.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
        except Exception as error:
            target.unlink(missing_ok=True)
            raise ValueError("Pinned public source unavailable: " + url + "; " + str(error)) from error
        if not verified(target, record):
            target.unlink(missing_ok=True)
            raise ValueError("Pinned public source size/SHA-256 differs: " + url)
        stat = target.stat()
        self.checked[url] = (identity, (stat.st_ino, stat.st_size, stat.st_mtime_ns))
        return target

    def render(self, recipe):
        kind = recipe["kind"]
        if kind == "url":
            return self.url(recipe).read_bytes()
        if kind == "literal":
            return base64.b64decode(recipe["base64"], validate=True)
        if kind == "zeros":
            return bytes(recipe["bytes"])
        if kind == "concat":
            return b"".join(self.render(part) for part in recipe["parts"])
        if kind == "tar-member":
            archive = self.url(recipe["archive"])
            member = recipe["member"]
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("Unsafe archive member")
            with tarfile.open(archive, "r:*") as stream:
                handle = stream.extractfile(member)
                if handle is None:
                    raise ValueError("Missing pinned archive member: " + member)
                data = handle.read()
            if (len(data) != recipe["bytes"] or
                    hashlib.sha256(data).hexdigest() != recipe["sha256"]):
                raise ValueError("Pinned archive member differs: " + member)
            return data
        if kind == "zip-member":
            archive = self.url(recipe["archive"])
            member = recipe["member"]
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("Unsafe ZIP member")
            with zipfile.ZipFile(archive) as stream:
                data = stream.read(member)
            if len(data) != recipe["bytes"] or hashlib.sha256(data).hexdigest() != recipe["sha256"]:
                raise ValueError("Pinned ZIP member differs")
            return data
        if kind == "ar-member":
            raw = self.url(recipe["archive"]).read_bytes()
            if not raw.startswith(b"!<arch>\n"):
                raise ValueError("Expected Debian ar archive")
            offset = 8
            while offset + 60 <= len(raw):
                header = raw[offset:offset + 60]
                size = int(header[48:58])
                name = header[:16].decode().strip().rstrip("/")
                data = raw[offset + 60:offset + 60 + size]
                if name == recipe["member"]:
                    if len(data) != recipe["bytes"] or hashlib.sha256(data).hexdigest() != recipe["sha256"]:
                        raise ValueError("Pinned Debian member differs")
                    return data
                offset += 60 + size + size % 2
            raise ValueError("Missing Debian data member")
        if kind == "png-rgb24":
            archive = self.url(recipe["archive"])
            output = self.cache / ("rgb24-" + recipe["sha256"])
            if not verified(output, recipe):
                subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-y",
                                "-i", str(archive), "-threads", "1", "-pix_fmt", "rgb24",
                                "-f", "rawvideo", str(output)], check=True)
                if not verified(output, recipe):
                    raise ValueError("Pinned RGB24 plane differs")
            return output.read_bytes()
        if kind == "png-recorded-timestamps":
            source = recipe["source"]
            url = source["url"]
            if not url.startswith("https://") or "@" in url.split("/")[2]:
                raise ValueError("Expected public PNG HTTPS source")
            target = self.cache / ("png-" + hashlib.sha256(url.encode()).hexdigest())
            raw = target.read_bytes() if target.is_file() else b""
            if len(raw) != source["bytes"] or png_stable_sha(raw) != source["stable_sha256"]:
                errors = []
                for candidate in [url, *source.get("alternate_urls", [])]:
                    try:
                        if not candidate.startswith("https://") or "@" in candidate.split("/")[2]:
                            raise ValueError("Expected public PNG HTTPS source")
                        request = urllib.request.Request(candidate, headers={"User-Agent": "optimbench-public-inputs/1"})
                        with urllib.request.urlopen(request, timeout=180) as response:
                            raw = response.read(source["bytes"] + 1)
                        if len(raw) != source["bytes"] or png_stable_sha(raw) != source["stable_sha256"]:
                            raise ValueError("PNG non-timestamp bytes differ")
                        break
                    except Exception as error:
                        errors.append(str(error))
                else:
                    raise ValueError("Pinned PNG source unavailable or differs: " + url + "; " + "; ".join(errors))
                target.write_bytes(raw)
            chunks = png_chunks(raw)
            actual = {index for index, chunk in enumerate(chunks) if png_time_chunk(chunk)}
            recorded = {row["index"] for row in recipe["recorded"]}
            if actual != recorded:
                raise ValueError("PNG timestamp layout differs")
            for row in recipe["recorded"]:
                chunk = base64.b64decode(row["base64"], validate=True)
                if not png_time_chunk(chunk) or len(png_chunks(b"\x89PNG\r\n\x1a\n" + chunk)) != 1:
                    raise ValueError("Only recorded PNG timestamp chunks may be restored")
                chunks[row["index"]] = chunk
            return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)
        raise ValueError("Unsupported public acquisition recipe: " + str(kind))


def acquire(root, cache=None):
    root = Path(root).resolve()
    manifest = json.loads((root / "payload-public.json").read_text())
    unresolved = [record["destination"] for record in manifest["files"]
                  if not record.get("fetch") and not verified(root / record["destination"], record)]
    if unresolved:
        raise ValueError(f"PAYLOAD PENDING [{manifest['task']}] (public): "
                         f"python3 tools/stage_payload.py --task {manifest['task']} "
                         "--from /path/to/payload\n" + "\n".join(unresolved))
    with tempfile.TemporaryDirectory(prefix="public-input-fetch-") as temporary:
        fetcher = Fetcher(cache or temporary)
        sources = {}
        def enumerate_sources(value):
            if isinstance(value, dict):
                if value.get("url") and "sha256" in value and "bytes" in value:
                    sources[value["url"]] = value
                for child in value.values():
                    enumerate_sources(child)
            elif isinstance(value, list):
                for child in value:
                    enumerate_sources(child)
        for record in manifest["files"]:
            if not verified(root / record["destination"], record) and record.get("fetch"):
                enumerate_sources(record["fetch"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetcher.url, sources.values()))
        for record in manifest["files"]:
            relative = Path(record["destination"])
            target = (root / relative).resolve()
            if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root):
                raise ValueError("Public acquisition destination escapes environment")
            if verified(target, record) or not record.get("fetch"):
                continue
            try:
                data = fetcher.render(record["fetch"])
                if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise ValueError("Reconstructed input differs from its frozen size/SHA-256")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_file = target.with_name("." + target.name + ".fetching")
                temporary_file.write_bytes(data)
                temporary_file.replace(target)
            except Exception as error:
                raise ValueError(f"PUBLIC INPUT FETCH FAILED [{manifest['task']}]: "
                                 f"{relative}; {error}; no replacement bytes permitted") from error


def check(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "payload-public.json").read_text())
    absent = []
    for record in manifest["files"]:
        relative = Path(record["destination"])
        target = (root / relative).resolve()
        if (relative.is_absolute() or ".." in relative.parts or
                not target.is_relative_to(root) or target.is_symlink()):
            raise ValueError("Unsafe public payload path")
        if (not target.is_file() or target.stat().st_size != record["bytes"]):
            absent.append(str(relative))
            continue
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != record["sha256"]:
            absent.append(str(relative))
    if absent:
        raise ValueError(f"PAYLOAD PENDING [{manifest['task']}] (public): "
                         f"python3 tools/stage_payload.py --task {manifest['task']} "
                         "--from /path/to/payload\n" + "\n".join(absent))


if __name__ == "__main__":
    try:
        args = sys.argv[1:]
        fetch = "--fetch" in args
        args = [arg for arg in args if arg != "--fetch"]
        root = args[0] if args else Path(__file__).resolve().parent
        if fetch:
            acquire(root)
        check(root)
    except (ValueError, OSError) as error:
        sys.exit(str(error))
