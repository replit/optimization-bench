#!/usr/bin/env python3
"""Author fetch-only GH Archive units from the exact externally audited hours."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

TASK = Path(__file__).resolve().parents[2]
ROOT = TASK.parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import canonical_json, b64


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def csv_record(values):
    fields = []
    for value in values:
        if value is None:
            fields.append("")
        elif isinstance(value, str):
            stream = io.StringIO(newline="")
            csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator="").writerow([value])
            fields.append(stream.getvalue())
        else:
            raise ValueError("Unexpected scalar in the frozen source")
    return (",".join(fields) + "\n").encode()


def oracle(directory, provenance):
    archive = directory / "events.json.gz"
    if (archive.stat().st_size != provenance["compressed_bytes"]
            or digest(archive) != provenance["compressed_sha256"]):
        raise ValueError("Audited transport changed")
    counter, identities = Counter(), set()
    decoded, size, csv_output = hashlib.sha256(), 0, io.BytesIO()
    with gzip.open(archive, "rb") as stream:
        for line in stream:
            decoded.update(line)
            size += len(line)
            if not line.strip():
                continue
            event = json.loads(line)
            identity = event["id"]
            if identity in identities:
                raise ValueError("Duplicate event identity")
            identities.add(identity)
            name = (event.get("repo") or {}).get("name")
            kind = event.get("type")
            if any(v is not None and not isinstance(v, str) for v in (name, kind)):
                raise ValueError("Unexpected grouping key")
            counter[name, kind] += 1
            csv_output.write(csv_record([identity, kind, name, event.get("created_at")]))
    if size != provenance["input_bytes"] or decoded.hexdigest() != provenance["input_sha256"]:
        raise ValueError("Audited decoded hour changed")
    scalar = lambda value: (0, "") if value is None else (1, value)
    grouped = [{"repository": key[0], "event_type": key[1], "count": count}
               for key, count in sorted(counter.items(), key=lambda row:
                   (-row[1], scalar(row[0][0]), scalar(row[0][1])))]
    outputs = {
        "group-gharchive": (json.dumps(grouped, ensure_ascii=False, separators=(",", ":"))
                           + "\n").encode(),
        "csv-gharchive": csv_output.getvalue(),
    }
    return identities, {operation: canonical_json({
        "exit_code": 0, "stdout_base64": b64(body), "stderr_base64": ""})
        for operation, body in outputs.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True,
                        help="External eligibility directory, never inside the repository")
    args = parser.parse_args()
    if args.corpus.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Keep event data outside the repository")
    sources = {split: json.loads((args.corpus / split / "provenance.json").read_text())
               for split in ("public", "private")}
    for source in sources.values():
        parsed = urlsplit(source["source_url"])
        if parsed.scheme != "https" or parsed.netloc != "data.gharchive.org" or parsed.query:
            raise ValueError("Expected the audited GH Archive publisher")
    if sources["public"]["source_url"].rsplit("/", 1)[-1][:10] == (
            sources["private"]["source_url"].rsplit("/", 1)[-1][:10]):
        raise ValueError("Public and private must be different days")
    authored = {split: oracle(args.corpus / split, source) for split, source in sources.items()}
    if authored["public"][0] & authored["private"][0]:
        raise ValueError("Public/private event overlap")

    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    lock_path = TASK / "tests/heldout.lock.json"
    lock = json.loads(lock_path.read_text())
    public_lock_path = TASK / "environment/public-workloads.lock.json"
    public_lock = json.loads(public_lock_path.read_text())
    new_public = []
    for split, source in sources.items():
        public = split == "public"
        scope = "public" if public else "heldout"
        inputs = TASK / ("environment/workload/inputs" if public else "tests/heldout")
        manifest_path = inputs / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        previous = [doc.copy() for batches in manifest["splits"].values()
                    for batch in batches for doc in batch["docs"]]
        for operation, expected in authored[split][1].items():
            identifier = f"{operation}-{split}"
            if any(doc["path"] == identifier for doc in previous):
                raise ValueError("GH Archive units already authored")
            doc = {
                "path": identifier, "batch": identifier.replace("-", "_"),
                "operation": operation, "sha256": source["compressed_sha256"],
                "bytes": source["compressed_bytes"],
                "decoded_bytes": source["input_bytes"], "decoded_sha256": source["input_sha256"],
                "expect_len": len(expected), "expect_sha": hashlib.sha256(expected).hexdigest(),
            }
            manifest["splits"].setdefault("dev" if public else "heldout", []).append({"docs": [doc]})
            if public:
                new_public.append(doc)
                public_lock["cases"].append({
                    "id": identifier,
                    "files_sha256": {"files/" + doc["sha256"]: doc["sha256"]}})
        current = [doc for batches in manifest["splits"].values()
                   for batch in batches for doc in batch["docs"]]
        assert all(doc in current for doc in previous)
        dump(manifest_path, manifest)
        destination = (inputs / "files" / source["compressed_sha256"]).relative_to(TASK).as_posix()
        pin = {"bytes": source["compressed_bytes"], "sha256": source["compressed_sha256"]}
        if public:
            entry = {"name": pin["sha256"], "scope": scope, "destination": destination, **pin,
                     "distribution": "upstream",
                     "fetch": {"kind": "url", "url": source["source_url"], **pin}}
        else:
            # The shared verifier fetches this outside Git before preparing cases.
            reference = {"scope": scope, "role": "input", "kind": "file",
                         "license_basis": "fetch-only; no redistribution",
                         # Same publisher bucket/object, accepted without browser headers.
                         "url": source["source_url"].replace(
                             "https://data.gharchive.org/",
                             "https://storage.googleapis.com/data.gharchive.org/"), **pin}
            lock.setdefault("references", {})[destination.removeprefix("tests/")] = reference
            entry = {"name": pin["sha256"], "destination": destination,
                     "retention": "fetch-only", **reference}
        payload["files"].append(entry)
    private_manifest = TASK / "tests/heldout/manifest.json"
    lock["files"]["heldout/manifest.json"] = digest(private_manifest)
    for entry in payload["files"]:
        if entry["destination"] == "tests/heldout/manifest.json":
            entry.update(bytes=private_manifest.stat().st_size, sha256=digest(private_manifest))
    public_lock["workload_manifest_sha256"] = digest(
        TASK / "environment/workload/inputs/manifest.json")
    public_lock["edition"] = "jq-gharchive-draft-20261029"
    dump(payload_path, payload)
    dump(lock_path, lock)
    dump(public_lock_path, public_lock)
    dump(TASK / "environment/payload-public.json", {
        "schema_version": 1, "task": TASK.name, "files": [
            {**entry, "destination": entry["destination"].removeprefix("environment/")}
            for entry in payload["files"] if entry["scope"] == "public"]})
    definition_path = TASK / "task.json"
    definition = json.loads(definition_path.read_text())
    definition.update(task_version="1.3.0", example_count=33,
        input_edition=public_lock["edition"],
        title="Strip OpenAPI descriptions, stream GeoJSON and process GH Archive events",
        job="Four equally weighted performance units share one jq build and image: the original "
            "native OpenAPI batch, GeoJSON feature streaming, complete-hour event grouping/counting/"
            "sorting, and complete-hour event-to-CSV conversion. Each CLI unit reads its original "
            "input once. Downloads, gzip decoding and integrity checks occur before timing.\n\n"
            "Before the verification commands below, stage the public files with "
            "`python3 tools/stage_payload.py --task jq-strip-openapi-descriptions --fetch`.\n\n"
            "The fetch-only description below applies to the new GH Archive private inputs. "
            "Existing private assets retain their original distribution. The unchanged-input "
            "statement below means no pre-existing input bytes or expectations changed. "
            "These additions have new input and complete-output commitments.",
        contract="Preserve original OpenAPI output bytes and supported jq behavior. All three CLI "
            "units compare complete stdout, stderr and exit status. Private GH Archive inputs "
            "are fetched outside Git. Raw-event redistribution terms are unconfirmed, so no "
            "GH Archive event body or derived output body is committed.")
    dump(definition_path, definition)
    dump(TASK / "tests/receipts/gharchive-authoring.json", {
        "public_source_url": sources["public"]["source_url"], "public_units": new_public,
        "complete_independent_oracles": True, "same_exact_eligibility_hours": True,
        "different_days_same_publisher": True, "cross_split_event_id_overlap": 0,
        "repository_or_actor_disjointness_claimed": False,
        "raw_event_redistribution_terms": "Unconfirmed. Fetch by pinned URL and SHA256.",
        "old_inputs_references_and_membership_unchanged": True,
        "expected_outputs": "Full stdout/stderr/status lengths and SHA256, not prefixes or samples.",
        "private_membership_or_bodies_emitted": False,
    })
    print(json.dumps({"new_operations": 2, "different_days_same_publisher": True,
                      "complete_independent_references_authored": True,
                      "raw_event_or_output_bodies_committed": False}))


if __name__ == "__main__":
    main()
