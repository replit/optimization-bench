#!/usr/bin/env python3
"""Describe frozen CLI recipes or stage the original public CLI fixture."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.parse import quote


TASK = Path(__file__).resolve().parents[2]
NOTICES = [
    ("citus", "LICENSE", 34520, "76a97c878c9c7a8321bb395c2b44d3fe2f8d81314d219b20138ed0e2dddd5182"),
    ("citus", "NOTICE", 4417, "27a04e3a405472059d373dcba15d440a55a807af4aabf828c2a69a5e735389bd"),
    ("pg_partman", "LICENSE.txt", 1201, "657cd7bde17fc758e47824280a7ccb9bc7d44067e3f3790c2ef4b07e8a04223a"),
    ("pgvector", "LICENSE", 1104, "6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87"),
    ("supabase", "LICENSE", 11338, "c29468abcd629e248c2f76848155c386b733872bcabcc4cf65ef1e7c61431042"),
    ("timescaledb", "LICENSE", 807, "0378e0948feefd85f579319c74d6e2b671194037f550c7176ef26649d94c895b"),
    ("timescaledb", "LICENSE-APACHE", 10174, "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594"),
    ("timescaledb", "NOTICE", 2468, "662ab6f419fbb458ca8802ad8fc8da7b2a79d850f4d0b5194dab78accb581e04"),
]


def literal(raw):
    return {"kind": "literal", "base64": base64.b64encode(raw).decode()}


def upstream(source, path, size, digest):
    repository = source["url"].removeprefix("https://github.com/")
    return {"kind": "url",
            "url": f"https://raw.githubusercontent.com/{repository}/{source['commit']}/{quote(path)}",
            "bytes": size, "sha256": digest}


def recipe(raw, document, sources):
    if len(raw) != document["size"] or hashlib.sha256(raw).hexdigest() != document["sha256"]:
        raise ValueError("Frozen CLI commitment mismatch")
    header, argv, transport, outputs, body = raw.split(b"\n", 4)
    if header != b"CLI1":
        raise ValueError("Missing CLI framing")
    parts = [literal(raw[:len(raw) - len(body)])]
    members = {(row.get("repo", row.get("source")), row["path"]): row
               for row in document["members"]}
    if transport == b"tar":
        cursor = 0
        with tarfile.open(fileobj=io.BytesIO(body)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                name, path = member.name.split("/", 1)
                expected = members[name, path]
                data = archive.extractfile(member).read()
                if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                    raise ValueError("Frozen tar member commitment mismatch")
                parts.extend([literal(body[cursor:member.offset_data]),
                              upstream(sources[name], path, len(data), expected["sha256"])])
                cursor = member.offset_data + member.size
        parts.append(literal(body[cursor:]))
    else:
        (name, path), expected = next(iter(members.items()))
        if len(members) != 1 or hashlib.sha256(body).hexdigest() != expected["sha256"]:
            raise ValueError("Frozen single-file commitment mismatch")
        parts.append(upstream(sources[name], path, len(body), expected["sha256"]))
    return {"kind": "concat", "parts": parts}


def stage_cli_fixture():
    from tools.payload_check import Fetcher

    digest = "f783e5a7c220b5cd5415914dfe6a511d6b9ac1326083a6751afa334f147acb6e"
    destination = f"environment/workload/inputs/files/{digest}"
    payload = json.loads((TASK / "payload.json").read_text())
    entry = next(row for row in payload["files"] if row["scope"] == "public"
                 and row["destination"] == destination and row["sha256"] == digest)
    raw = Fetcher(TASK.parents[1] / ".cache/public-inputs").render(entry["fetch"])
    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Original public CLI fixture commitment mismatch")
    target = TASK / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    print(f"Authenticated original CLI fixture: {digest} ({len(raw)} bytes)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--stage-cli-fixture", action="store_true")
    args = parser.parse_args()
    if args.stage_cli_fixture:
        stage_cli_fixture()
        return
    manifest = json.loads((TASK / "environment/workload/inputs/manifest.json").read_text())
    documents = {doc["sha256"]: doc for groups in manifest["splits"].values()
                 for group in groups for doc in group["docs"]}
    path = TASK / "payload.json"
    payload = json.loads(path.read_text())
    for entry in payload["files"]:
        if entry["scope"] == "public" and entry["sha256"] in documents:
            entry["fetch"] = recipe((TASK / entry["destination"]).read_bytes(),
                                    documents[entry["sha256"]], manifest["source"])
            entry["distribution"] = "upstream"
    for name, source_path, size, digest in NOTICES:
        destination = "environment/notices/cli-" + name + "--" + source_path.replace("/", "--")
        record = {"scope": "public", "name": destination.removeprefix("environment/"),
                  "destination": destination, "bytes": size, "sha256": digest,
                  "url": manifest["source"][name]["url"], "distribution": "upstream",
                  "fetch": upstream(manifest["source"][name], source_path, size, digest)}
        payload["files"] = [entry for entry in payload["files"] if entry["destination"] != destination]
        payload["files"].append(record)
    if args.write:
        path.write_text(json.dumps(payload, indent=2) + "\n")
    elif payload != json.loads(path.read_text()):
        raise SystemExit("Frozen CLI acquisition recipes need --write")
    print(f"Authenticated {len(documents)} unchanged CLI inputs and {len(NOTICES)} notice pins")


if __name__ == "__main__":
    main()
