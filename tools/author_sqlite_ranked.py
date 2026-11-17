#!/usr/bin/env python3
"""Author a NEW ranked-document packet, never regenerate an existing split.

The destination must be empty. Private packets must stay outside the agent image.
Only aggregate counts are printed. The pinned pristine CLI and protected driver
are provided by the operator, not discovered on PATH.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import urllib.request


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sections(path, text):
    lines = text.splitlines()
    headings = []
    for n in range(len(lines) - 1):
        title, underline = lines[n:n + 2]
        if (title and title == title.strip() and len(underline) >= 4
                and len(set(underline)) == 1 and underline[0] in "=-~^*+"
                and len(underline) >= len(title)):
            headings.append((n, title))
    for i, (start, title) in enumerate(headings):
        stop = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 2:stop]).strip()
        if body:
            yield f"{path}:{start + 1}", title, body


def corpus(archive, spec, require_requests=True):
    texts, notices, inventory = {}, {}, []
    generated = re.compile(
        r"^\.\.\s+(?:(?:this|the)\s+(?:file|document)\s+(?:is|was)\s+)?"
        r"(?:auto[- ]generated|automatically generated|generated\s+(?:file|document)|do not edit)\b",
        re.I)
    rights_notice = re.compile(
        r"copyright|licen[cs]e|all rights reserved|permission|redistribut|"
        r"creative commons|public domain|CC[- ]BY|CC0|0BSD", re.I)
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != spec["archive_prefix"]
                    or ".." in parts or member.name.startswith("/")):
                raise ValueError("Unsafe or unexpected source archive")
            path = "/".join(parts[1:])
            selected = (path.startswith(spec["document_root"])
                        and any(path.endswith(s) for s in spec["suffixes"]))
            if not selected and path not in spec["notices"]:
                continue
            if not member.isfile():
                raise ValueError("Selected source is not a regular file")
            raw = stream.extractfile(member).read()
            if path in spec["notices"]:
                notices[path] = raw
            if (not selected or path in spec["exclude_exact_paths"]
                    or any(path.startswith(p) for p in spec["exclude_path_prefixes"])):
                continue
            text = raw.decode("utf-8")
            if any(generated.search(line) for line in text.splitlines()[:50]):
                continue
            # Keep complete source files rather than guessing which surrounding
            # sentences are part of an embedded notice.
            if rights_notice.search(text):
                notices[path] = raw
            if path in texts:
                raise ValueError("Duplicate source archive member")
            texts[path] = text
            inventory.append({"path": path, "bytes": len(raw), "sha256": digest(raw)})
    if not set(spec["notices"]).issubset(notices):
        raise ValueError("Required license notice missing")
    docs, requests, seen_docs, seen_requests = [], [], set(), set()
    for path, text in sorted(texts.items()):
        for origin, title, body in sections(path, text):
            fingerprint = digest((title + "\0" + body).encode())
            if fingerprint not in seen_docs:
                seen_docs.add(fingerprint)
                docs.append((len(docs) + 1, origin, title, body))
            if path.startswith(spec["question_root"]) and title.endswith("?"):
                words = dict.fromkeys(re.findall(r"[A-Za-z0-9]+", title.lower()))
                match = " OR ".join('"' + word + '"' for word in words)
                if match and match not in seen_requests:
                    if any("\t" in field or "\n" in field for field in (origin, title, match)):
                        raise ValueError("Question cannot be represented without alteration")
                    seen_requests.add(match)
                    requests.append((origin, title, match))
    if not docs or (require_requests and not requests):
        raise ValueError("No eligible documents or questions")
    return docs, requests, sorted(inventory, key=lambda row: row["path"]), notices, seen_docs


def project_set(spec):
    return {source["repository"].lower().rstrip("/").removesuffix(".git")
            for source in spec.get("sources", [spec])}


def combined_corpus(archive, spec, excluded=()):
    sources = spec.get("sources", [spec])
    if len(project_set(spec)) != len(sources):
        raise ValueError("Each source project may appear only once")
    docs, requests, inventory, notices = [], [], [], {}
    seen_docs, seen_requests, excluded = set(), set(), set(excluded)
    removed, source_ids = set(), set()
    for source in sources:
        prefix = source.get("id", "") if "sources" in spec else ""
        if "sources" in spec:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", prefix) or prefix in source_ids:
                raise ValueError("Source IDs must be unique safe names")
            source_ids.add(prefix)
        source_archive = archive / (prefix + ".tar.gz") if prefix else archive
        raw = source_archive.read_bytes()
        if len(raw) != source["archive_bytes"] or digest(raw) != source["archive_sha256"]:
            raise ValueError("Source archive commitment differs")
        source_docs, source_requests, source_inventory, source_notices, _ = corpus(
            source_archive, source, require_requests="sources" not in spec)
        label = prefix + "/" if prefix else ""
        for _, origin, title, body in source_docs:
            fingerprint = digest((title + "\0" + body).encode())
            if fingerprint in excluded:
                removed.add(fingerprint)
            elif fingerprint not in seen_docs:
                seen_docs.add(fingerprint)
                docs.append((len(docs) + 1, label + origin, title, body))
        for origin, title, match in source_requests:
            if match not in seen_requests:
                seen_requests.add(match)
                requests.append((label + origin, title, match))
        inventory.extend({**row, "path": label + row["path"]} for row in source_inventory)
        notices.update({label + name: data for name, data in source_notices.items()})
    if not docs or not requests:
        raise ValueError("No eligible combined documents or questions")
    return docs, requests, inventory, notices, seen_docs, len(removed)


def build(archive, spec, output, sqlite, driver, excluded=()):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refuse to overwrite an authored packet")
    output.mkdir(parents=True, exist_ok=True)
    docs, requests, inventory, notices, fingerprints, removed = combined_corpus(archive, spec, excluded)
    quote = lambda text: "'" + text.replace("'", "''") + "'"
    sql = [
        "PRAGMA page_size=4096;", "PRAGMA journal_mode=DELETE;", "BEGIN;",
        "CREATE TABLE docs(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL);",
        "CREATE VIRTUAL TABLE docs_fts USING fts5(title,body,content=docs,content_rowid=id,tokenize='unicode61',detail=full,columnsize=1);",
    ]
    sql += ["INSERT INTO docs VALUES(" + str(row[0]) + "," +
            ",".join(quote(value) for value in row[1:]) + ");" for row in docs]
    sql += ["INSERT INTO docs_fts(docs_fts) VALUES('rebuild');", "COMMIT;"]
    sql_bytes = ("\n".join(sql) + "\n").encode()
    database = output / "input.db"
    subprocess.run([str(sqlite), str(database)], input=sql_bytes,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    queries = output / "requests.tsv"
    queries.write_text("".join("\t".join(row) + "\n" for row in requests))
    for name in ("expected.bin", "original-again.bin"):
        subprocess.run([str(driver), str(database), str(queries), str(output / name), "plain"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    if (output / "expected.bin").read_bytes() != (output / "original-again.bin").read_bytes():
        raise ValueError("Original against itself differs")
    (output / "original-again.bin").unlink()
    artifacts = {name: {"bytes": (output / name).stat().st_size,
                        "sha256": digest((output / name).read_bytes())}
                 for name in ("input.db", "requests.tsv", "expected.bin")}
    save(output / "case.json", {
        "family": "ranked-documents", "path": spec["case_id"],
        **artifacts["input.db"], "requests": len(requests),
        "requests_bytes": artifacts["requests.tsv"]["bytes"],
        "requests_sha256": artifacts["requests.tsv"]["sha256"],
        "expect_len": artifacts["expected.bin"]["bytes"],
        "expect_sha": artifacts["expected.bin"]["sha256"],
    })
    save(output / "provenance.json", {
        "recipe_version": 2, "source": spec, "source_files": inventory,
        "documents": len(docs), "requests": len(requests),
        "creation_sql_sha256": digest(sql_bytes), "artifacts": artifacts,
        "original_again_equal": True, "qualification": "not performed",
        "sqlite_cli_sha256": digest(sqlite.read_bytes()),
        "driver_sha256": digest(driver.read_bytes()),
        "excluded_cross_split_documents": removed,
    })
    save(output / "document-fingerprints.json", sorted(fingerprints))
    for name, data in notices.items():
        target = output / "notices" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"projects": len(project_set(spec)), "files": len(inventory),
            "documents": len(docs), "requests": len(requests),
            "excluded_cross_split_documents": removed,
            "original_again_equal": True, "qualification": "not performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--exclude-fingerprints", type=Path)
    args = parser.parse_args()
    if not all((args.spec, args.archive, args.output, args.sqlite, args.driver)):
        parser.error("Authoring requires spec, archive, output, sqlite and driver")
    spec = json.loads(args.spec.read_text())
    if "sources" in spec:
        identifiers = [source.get("id", "") for source in spec["sources"]]
        if (not identifiers or len(set(identifiers)) != len(identifiers)
                or any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value)
                       for value in identifiers)):
            raise ValueError("Source IDs must be unique safe names")
    for source in spec.get("sources", [spec]):
        target = args.archive / (source["id"] + ".tar.gz") if "sources" in spec else args.archive
        if args.fetch and not target.exists():
            if not source["archive_url"].startswith("https://"):
                raise ValueError("HTTPS source required")
            with urllib.request.urlopen(source["archive_url"], timeout=180) as response:
                raw = response.read(source["archive_bytes"] + 1)
            if len(raw) != source["archive_bytes"] or digest(raw) != source["archive_sha256"]:
                raise ValueError("Source download commitment differs")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    excluded = json.loads(args.exclude_fingerprints.read_text()) if args.exclude_fingerprints else []
    print(json.dumps(build(args.archive, spec, args.output, args.sqlite, args.driver, excluded)))


if __name__ == "__main__":
    main()
