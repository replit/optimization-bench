#!/usr/bin/env python3
"""Reproduce the public Toronto excerpt, never acquire data during execution."""
import argparse
import codecs
import hashlib
import json
from pathlib import Path
import re
import urllib.request

URL = ("https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/"
       "6ac4569e-fd37-4cbc-ac63-db3624c5f6a2/resource/"
       "d6089672-bdf7-4857-8ea8-90da826fcfa1/download/street-tree-data-4326.geojson")
PREFIX_BYTES = 16_777_216
PREFIX_SHA = "0a9e9f66fd26769da78a88363764b4fb93ed13b35e187aea78032ff445aa6647"
OUTPUT_BYTES = 12_396_533
OUTPUT_SHA = "293d2db480d4a787168805c02ea53ace1e96a636af3dcea0a6e07b113d35c941"
FEATURES = 25_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def extract(raw):
    if len(raw) != PREFIX_BYTES or sha(raw) != PREFIX_SHA:
        raise ValueError("Publisher snapshot prefix changed")
    text = codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
    match = re.search(r'"features"\s*:\s*\[', text)
    if match is None:
        raise ValueError("Feature array absent")
    cursor = match.end()
    while text[cursor].isspace():
        cursor += 1
    first = cursor
    decoder = json.JSONDecoder()
    identities = set()
    for index in range(FEATURES):
        feature, end = decoder.raw_decode(text, cursor)
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("Expected original GeoJSON features")
        identities.add(sha(json.dumps(feature, sort_keys=True).encode()))
        cursor = end
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if index + 1 < FEATURES:
            if text[cursor] != ",":
                raise ValueError("Feature separator missing")
            cursor += 1
            while text[cursor].isspace():
                cursor += 1
    result = b'{"type":"FeatureCollection","features":[\n' + text[first:end].encode() + b"\n]}\n"
    if len(identities) != FEATURES or len(result) != OUTPUT_BYTES or sha(result) != OUTPUT_SHA:
        raise ValueError("Public excerpt differs from its frozen commitment")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prefix", type=Path, help="Offline authenticated publisher prefix")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.source_prefix:
        raw = args.source_prefix.read_bytes()
    else:
        request = urllib.request.Request(URL, headers={
            "Range": f"bytes=0-{PREFIX_BYTES-1}", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206 or response.headers.get("Content-Range") != (
                    f"bytes 0-{PREFIX_BYTES-1}/342951476"):
                raise ValueError("Publisher did not return the pinned range")
            raw = response.read(PREFIX_BYTES + 1)
    body = extract(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != body:
            raise ValueError("Refusing to replace different input bytes")
    else:
        with args.output.open("xb") as output:
            output.write(body)
    print(json.dumps({"bytes": len(body), "sha256": sha(body), "features": FEATURES}))


if __name__ == "__main__":
    main()
