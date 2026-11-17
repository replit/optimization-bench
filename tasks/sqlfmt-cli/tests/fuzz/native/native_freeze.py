#!/usr/bin/env python3
"""Freeze the approved origin-separated native pair without altering legacy data.

Consumes the private authoring handoff, verifies pinned archives and complete
original outputs, then replaces only the diagnostic native additions. Prints
aggregate counts only. This is authoring tooling, never candidate execution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import quote

from native_author import baseline, dump
from native_provenance import verify_build

TASK = Path(__file__).resolve().parents[3]
REPO = TASK.parents[1]
EDITION = "native-origins-v1"
SIZES = {"public": (695, 14817799), "private": (2014, 5267026)}
ORIGINS = {
    "public": {"postgres/postgres", "postgis/postgis"},
    "private": {"mozilla/bigquery-etl"},
}
LICENSES = {
    "postgres/postgres": "PostgreSQL",
    "postgis/postgis": "PostGIS licenses, see retained COPYING and LICENSE.TXT",
    "mozilla/bigquery-etl": "MPL-2.0",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def historical(ref, name):
    return json.loads(subprocess.check_output(
        ["git", "show", f"{ref}:tasks/sqlfmt-cli/{name}"], cwd=REPO))


def authenticate(handoff, pins):
    """Authenticate every selected byte against its whole, pinned source archive."""
    prepared = {}
    for scope, (count, size) in SIZES.items():
        source = handoff["sets"][scope]
        manifest = load(source["manifest"])
        docs = manifest["docs"]
        assert len(docs) == count and sum(row["bytes"] for row in docs) == size
        assert len({row["id"] for row in docs}) == len({row["sha256"] for row in docs}) == count
        assert all(re.fullmatch(r"[A-Za-z0-9_.-]+", row["id"]) for row in docs)
        origins = load(source["origin_records"])
        by_id = {row["id"]: row for row in origins}
        assert len(by_id) == count and set(by_id) == {row["id"] for row in docs}
        assert {row["source"] for row in origins} == ORIGINS[scope]
        assert not any(row["source"] == "postgis/postgis"
                       and row["source_path"].startswith("extras/ogc_test_suite/")
                       for row in origins), "The historical OGC grant was not authenticated"
        items = []
        for row in docs:
            data = (Path(source["body_directory"]) / row["sha256"]).read_bytes()
            assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
            assert by_id[row["id"]]["sha256"] == row["sha256"]
            items.append({"id": row["id"], "data": data})
        for repository in ORIGINS[scope]:
            pin = pins[repository]
            assert pin["scope"] == scope and digest(pin["archive_path"]) == pin["archive_sha256"]
            selected = {row["source_path"]: row for row in origins if row["source"] == repository}
            assert len(selected) == sum(row["source"] == repository for row in origins)
            assert all(row["commit"] == pin["commit"] for row in selected.values())
            notices = {row["archive_path"]: row for row in pin["retained_license_notices"]}
            found, found_notices = set(), set()
            with tarfile.open(pin["archive_path"], "r|gz") as archive:
                for member in archive:
                    relative = member.name.partition("/")[2]
                    if not member.isfile() or relative not in selected.keys() | notices.keys():
                        continue
                    data = archive.extractfile(member).read()
                    value = hashlib.sha256(data).hexdigest()
                    if relative in selected:
                        assert value == selected[relative]["sha256"]
                        found.add(relative)
                    if relative in notices:
                        notice = notices[relative]
                        assert value == notice["sha256"] == digest(notice["retained_path"])
                        found_notices.add(relative)
            assert found == selected.keys() and found_notices == notices.keys()
        prepared[scope] = {"source": source, "docs": docs, "origins": origins, "items": items}
    assert not ({row["sha256"] for row in prepared["public"]["docs"]}
                & {row["sha256"] for row in prepared["private"]["docs"]})
    return prepared


def freeze(handoff_path, original_ref):
    current = load(TASK / "environment/workload/inputs/native-batch.json")
    assert current["edition"] == "diagnostic-v1" and not current.get("frozen")
    handoff = load(handoff_path)
    pins = {row["repository"]: row for row in load(handoff["pinned_archive_metadata"])}
    assert set(pins) == set.union(*ORIGINS.values())
    prepared = authenticate(handoff, pins)
    receipt_path = Path(handoff["original_build_receipt"])
    assert digest(receipt_path) == handoff["original_build_receipt_sha256"]
    verify_build(receipt_path.parent)
    binding = digest(receipt_path)
    for scope, data in prepared.items():
        reference = baseline(receipt_path.parent / "sqlfmt-native", data["items"])
        assert reference == Path(data["source"]["complete_framed_reference"]).read_bytes()
        results = json.loads(reference.split(b"\n", 2)[2])
        errors = sum(row["error_base64"] is not None for row in results)
        assert errors == (4 if scope == "private" else 0)
        data["reference"] = reference
        data["manifest"] = {
            "format": "sqlfmt-native-batch-v1", "edition": EDITION, "frozen": True,
            "reference_build_sha256": binding, "docs": data["docs"],
            "expected_returned_errors": errors,
            "expected": {"expect_len": len(reference), "expect_sha": hashlib.sha256(reference).hexdigest()},
        }

    original_lock = historical(original_ref, "tests/heldout.lock.json")
    original_payload = historical(original_ref, "payload.json")
    lock = load(TASK / "tests/heldout.lock.json")
    payload = load(TASK / "payload.json")
    assert len(original_lock["files"]) == 908
    assert all(lock["files"].get(key) == value for key, value in original_lock["files"].items())
    retired = set(lock["files"]) - original_lock["files"].keys()
    assert len(retired) == 6 and lock["native_addition"]["edition"] == "diagnostic-v1"
    retired_destinations = {"tests/" + key for key in retired}
    retired_destinations |= {"environment/workload/inputs/files/" + row["sha256"] for row in current["docs"]}
    payload["files"] = [row for row in payload["files"] if row["destination"] not in retired_destinations]
    originals = {row["destination"]: row for row in original_payload["files"]}
    assert {row["destination"]: row for row in payload["files"]} == originals
    for row in prepared["private"]["docs"]:
        key = "heldout/files/" + row["sha256"]
        assert key not in original_lock["files"]
        assert not (TASK / "tests" / key).exists() or key in retired
    # Nothing has been changed before all archives, references and old metadata pass.
    for key in retired:
        (TASK / "tests" / key).unlink()
    lock["files"] = dict(original_lock["files"])
    shutil.copy2(receipt_path, TASK / "tests/fuzz/native/native-reference-build.json")
    public = prepared["public"]
    inputs = TASK / "environment/workload/inputs"
    public_origins = {row["id"]: row for row in public["origins"]}
    for row in public["docs"]:
        source = Path(public["source"]["body_directory"]) / row["sha256"]
        destination = inputs / "files" / row["sha256"]
        shutil.copy2(source, destination)
        origin = public_origins[row["id"]]
        url = (f"https://raw.githubusercontent.com/{origin['source']}/{origin['commit']}/"
               + quote(origin["source_path"], safe="/"))
        payload["files"].append({
            "name": "native-" + row["id"], "scope": "public",
            "destination": destination.relative_to(TASK).as_posix(),
            "bytes": row["bytes"], "sha256": row["sha256"], "distribution": "upstream",
            "fetch": {"kind": "url", "url": url, "bytes": row["bytes"], "sha256": row["sha256"]},
            "license": LICENSES[origin["source"]],
        })
    dump(inputs / "native-batch.json", public["manifest"])
    (TASK / "tests/fuzz/native/native-public-reference.bin").write_bytes(public["reference"])
    dump(TASK / "tests/fuzz/native/native-public-origins.json", public["origins"])

    private = prepared["private"]
    hidden = TASK / "tests/heldout"
    additions = []
    for row in private["docs"]:
        target = hidden / "files" / row["sha256"]
        assert not target.exists(), "Refuse replacing a historical private object"
        shutil.copy2(Path(private["source"]["body_directory"]) / row["sha256"], target)
        additions.append(target)
    dump(hidden / "native-batch.json", private["manifest"])
    dump(hidden / "native-origins.json", private["origins"])
    (hidden / "references/native-batch.bin").write_bytes(private["reference"])
    additions += [hidden / "native-batch.json", hidden / "native-origins.json",
                  hidden / "references/native-batch.bin"]
    for index, path in enumerate(additions):
        value = digest(path)
        lock["files"][path.relative_to(TASK / "tests").as_posix()] = value
        payload["files"].append({
            "scope": "heldout", "name": "native-private-" + str(index),
            "destination": path.relative_to(TASK).as_posix(), "bytes": path.stat().st_size, "sha256": value,
        })
    assert all(lock["files"][key] == value for key, value in original_lock["files"].items())
    assert len({row["destination"] for row in payload["files"]}) == len(payload["files"])
    assert len({row["name"] for row in payload["files"]}) == len(payload["files"])
    lock["native_addition"] = {
        "edition": EDITION, "frozen": True, "qualification": False,
        "origin_separation": "Whole origins are disjoint within the native workload only.",
        "dialect_policy": "Authentic BigQuery SQL is accepted. Complete unchanged formatter output is the oracle.",
        "freeze_limit": "A/A is an unofficial noise check. Historical CLI data is unchanged.",
    }
    dump(TASK / "tests/heldout.lock.json", lock)
    dump(TASK / "payload.json", payload)
    projection_path = TASK / "environment/payload-public.json"
    projection = load(projection_path)
    projection["files"] = [{**row, "destination": row["destination"].removeprefix("environment/")}
                           for row in payload["files"] if row["scope"] == "public"]
    dump(projection_path, projection)
    public_lock_path = TASK / "environment/public-workloads.lock.json"
    public_lock = load(public_lock_path)
    native_case = next(row for row in public_lock["cases"] if row["id"] == "native-batch")
    native_case.update(split="native-frozen", files_sha256={row["sha256"]: row["sha256"] for row in public["docs"]})
    public_lock_path.write_text(json.dumps(public_lock, indent=1) + "\n")
    pinned = []
    for pin in pins.values():
        entry = {key: pin[key] for key in ("repository", "scope", "commit", "archive_url", "archive_sha256")}
        entry["archive_bytes"] = Path(pin["archive_path"]).stat().st_size
        entry["notices"] = []
        for notice in pin["retained_license_notices"]:
            name = pin["repository"].split("/")[1] + "-native-" + Path(notice["archive_path"]).name
            if pin["repository"] == "postgres/postgres":
                name = "PostgreSQL-native-COPYRIGHT.txt"
            parent = TASK / ("environment/notices" if pin["scope"] == "public"
                             else "tests/fuzz/native/notices")
            parent.mkdir(parents=True, exist_ok=True)
            target = parent / name
            shutil.copy2(notice["retained_path"], target)
            entry["notices"].append({"path": target.relative_to(TASK).as_posix(), "sha256": notice["sha256"]})
        pinned.append(entry)
    dump(TASK / "tests/fuzz/native/native-corpus.json", {
        "edition": EDITION, "frozen": True, "qualified": False, "sources": pinned,
        "sizes": {scope: {"documents": count, "bytes": size} for scope, (count, size) in SIZES.items()},
        "origin_separation": "Whole origins are disjoint within the native workload. No global historical uniqueness claim.",
        "cross_set_exact_body_hash_overlap": 0, "accepted_private_returned_errors": 4,
        "oracle": "Complete unchanged formatter output, not an independent SQL semantic oracle.",
        "public_license_exclusion": {
            "repository": "postgis/postgis", "prefix": "extras/ogc_test_suite/",
            "documents": 3, "bytes": 63097,
            "reason": "The applicable historical OGC redistribution grant could not be authenticated.",
        },
        "historical_private_hash_exclusions": {
            "documents": 598, "bytes": 2607402,
            "reason": "Preserve original private input secrecy without exposing matching identities.",
        },
        "single_pass": True, "historical_private_commitments_preserved": 908,
    })
    print(json.dumps({"frozen_edition": EDITION, "public_documents": 695, "private_documents": 2014,
                      "private_returned_errors_preserved": 4, "original_private_commitments_preserved": 908,
                      "private_membership_printed": False, "qualified": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--original-ref", required=True)
    args = parser.parse_args()
    freeze(args.handoff, args.original_ref)
