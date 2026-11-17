#!/usr/bin/env python3
"""Trusted one-time corpus authoring. Emits aggregates, never private membership/results."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import shutil

TASK = Path(__file__).resolve().parents[2]
ROOT = TASK.parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Case, Context, run_case
from docker_executor import DockerExecutor
spec = importlib.util.spec_from_file_location("geojson_acquisition", TASK / "environment/workload/fetch_inputs.py")
acquire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquire)
spec = importlib.util.spec_from_file_location("geojson_task", TASK / "environment/workload/task.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
PUBLIC_ID = "geojson-features-public"
PRIVATE_ID = "geojson-features-private"
SOURCE_SHA = "84850c37c629a5bee283bf2395ba23724cfb3e82ede6472b3be5e86534867f84"
SOURCE_BYTES = 342_951_476


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_source(source):
    if len(source) != SOURCE_BYTES or sha(source) != SOURCE_SHA:
        raise ValueError("Complete publisher snapshot changed")


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Refusing to replace existing authored material")
    else:
        with path.open("xb") as output:
            output.write(data)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def sync_public_payload():
    """Derive the standard public guard manifest without opening private bodies.

    This is not a catalog refresh. Its separate private-availability checks
    remain mandatory and may stay blocked when old LFS bodies are absent.
    """
    payload = json.loads((TASK / "payload.json").read_text())
    subset = {"schema_version": 1, "task": TASK.name, "files": [
        {**entry, "destination": entry["destination"].removeprefix("environment/")}
        for entry in payload["files"] if entry["scope"] == "public"]}
    dump(TASK / "environment/payload-public.json", subset)
    source = TASK / "notices/TORONTO-LICENSE.txt"
    destination = TASK / "environment/notices/TORONTO-LICENSE.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def original_result(body, work, image, build):
    work.mkdir()
    inputs, scratch = work / "inputs", work / "execution"
    inputs.mkdir()
    scratch.mkdir()
    (inputs / sha(body)).write_bytes(body)
    doc = {"path": "geojson-output", "bytes": len(body), "sha256": sha(body),
           "operation": "stream-geojson"}
    case = Case("author-geojson", {"docs": [doc]})
    workload = TASK / "environment/workload"
    context = Context(scratch, build, inputs, workload, workload, docker=True)
    observations, results = run_case(adapter, case, context, DockerExecutor(image, cpuset="0-7"))
    result = results[0]
    stdout, stderr = result.stdout, result.stderr
    (work / "stdout").write_bytes(stdout)
    (work / "stderr").write_bytes(stderr)
    if result.exit_code or stderr or result.leftovers:
        raise ValueError("Pristine GeoJSON operation failed. Details retained privately.")
    source = json.loads(body)["features"]
    actual = [json.loads(line) for line in stdout.splitlines()]
    if actual != source:
        raise ValueError("Complete original feature output differs")
    return observations["geojson-output"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--image", default="optimbench:jq-stream")
    parser.add_argument("--build", type=Path, required=True, help="Original rebuilt source tree, not an installed jq")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = args.source.read_bytes()
    validate_source(source)
    public = acquire.extract(source[:acquire.PREFIX_BYTES])
    public_features = json.loads(public)["features"]
    all_features = json.loads(source)["features"]
    # A meaningful group split. All streets seen in the public excerpt are
    # excluded from the new private set, including all records on those streets.
    public_groups = {f["properties"]["STREETNAME"] for f in public_features}
    private_features = [f for f in all_features
                        if f["properties"]["STREETNAME"]
                        and f["properties"]["STREETNAME"] not in public_groups]
    public_rows = {sha(encode(f)) for f in public_features}
    private_rows = {sha(encode(f)) for f in private_features}
    private_groups = {f["properties"]["STREETNAME"] for f in private_features}
    if (not private_features or len(public_rows) != len(public_features)
            or len(private_rows) != len(private_features)
            or public_rows & private_rows or public_groups & private_groups):
        raise ValueError("Input uniqueness/group disjointness failed")
    private = encode({"type": "FeatureCollection", "features": private_features})
    # Make only the parent usable by the isolated pristine process. Private
    # source membership and reference bodies are never emitted to stdout.
    args.work.chmod(0o755)
    records, payloads = {}, []
    for scope, body, identifier, batch in [
        ("public", public, PUBLIC_ID, "stream_geojson_public"),
        ("heldout", private, PRIVATE_ID, "stream_geojson_private"),
    ]:
        reference = original_result(body, args.work / scope, args.image, args.build)
        reference_again = original_result(body, args.work / (scope + "-repeat"), args.image, args.build)
        if reference != reference_again:
            raise ValueError("Pristine complete observations did not repeat")
        destination = TASK / ("environment/workload/inputs" if scope == "public" else "tests/heldout")
        write_new(destination / "files" / sha(body), body)
        record = {"path": identifier, "sha256": sha(body), "bytes": len(body),
                  "batch": batch, "operation": "stream-geojson",
                  "expect_len": len(reference), "expect_sha": sha(reference)}
        manifest_path = destination / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        existing = [d for batches in manifest["splits"].values() for b in batches for d in b["docs"]]
        if any(d["path"] == identifier for d in existing):
            raise ValueError("Expansion was already authored")
        split = "dev" if scope == "public" else "heldout"
        manifest["splits"].setdefault(split, []).append({"docs": [record]})
        dump(manifest_path, manifest)
        entry = {"name": sha(body), "scope": scope,
                 "destination": (destination / "files" / sha(body)).relative_to(TASK).as_posix(),
                 "bytes": len(body), "sha256": sha(body), "license": "Open Government Licence - Toronto"}
        if scope == "public":
            stored = TASK / "public/files" / sha(body)
            write_new(stored, body)
            entry.update(kind="repo-lfs", source=stored.relative_to(TASK).as_posix(),
                         distribution="operator",
                         reason="Frozen source-derived excerpt has no upstream URL for its exact prepared bytes. Reproduce with workload/fetch_inputs.py, then authenticate with standard staging.")
        else:
            entry["distribution"] = "git"
            expected = TASK / "tests/heldout/expected/geojson-features-private.json"
            write_new(expected, reference)
            payloads.append({"name": expected.name, "scope": "heldout",
                             "destination": expected.relative_to(TASK).as_posix(),
                             "bytes": len(reference), "sha256": sha(reference), "distribution": "git"})
        records[scope] = record
        payloads.append(entry)
    public_lock_path = TASK / "environment/public-workloads.lock.json"
    public_lock = json.loads(public_lock_path.read_text())
    public_lock["cases"].append({
        "id": PUBLIC_ID,
        "files_sha256": {"files/" + records["public"]["sha256"]: records["public"]["sha256"]}})
    public_lock["workload_manifest_sha256"] = sha(
        (TASK / "environment/workload/inputs/manifest.json").read_bytes())
    public_lock["edition"] = "jq-geojson-draft-20261028"
    dump(public_lock_path, public_lock)
    private_provenance = TASK / "tests/heldout/references/geojson-provenance.json"
    write_new(private_provenance, encode({
        "source_url": acquire.URL, "source_bytes": len(source), "source_sha256": sha(source),
        "groups": sorted(private_groups), "record": records["heldout"],
        "selection": "All original records on nonempty STREETNAME groups absent from the public excerpt",
        "transformation": "Original feature values retained, complete collection serialized once",
        "public_private_group_overlap": 0, "public_private_row_overlap": 0}))
    payloads.append({"name": private_provenance.name, "scope": "heldout",
                     "destination": private_provenance.relative_to(TASK).as_posix(),
                     "bytes": private_provenance.stat().st_size,
                     "sha256": sha(private_provenance.read_bytes()), "distribution": "git"})
    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    for entry in payload["files"]:
        if entry["destination"] == "tests/heldout/manifest.json":
            manifest_bytes = (TASK / entry["destination"]).read_bytes()
            entry.update(bytes=len(manifest_bytes), sha256=sha(manifest_bytes))
    payload["files"].extend(payloads)
    dump(payload_path, payload)
    lock_path = TASK / "tests/heldout.lock.json"
    lock = json.loads(lock_path.read_text())
    for path in [
        TASK / "tests/heldout/manifest.json", private_provenance,
        TASK / "tests/heldout/files" / records["heldout"]["sha256"],
        TASK / "tests/heldout/expected/geojson-features-private.json",
    ]:
        lock["files"][path.relative_to(TASK / "tests").as_posix()] = sha(path.read_bytes())
    dump(lock_path, lock)
    sync_public_payload()
    public_receipt = {
        "scope": "New workload authoring only. Not host-qualified.",
        "source_url": acquire.URL, "source_bytes": len(source), "source_sha256": sha(source),
        "public": records["public"], "public_features": len(public_features),
        "heldout_group_split_checked": True, "input_row_overlap": 0,
        "complete_pristine_output_checked": True, "pristine_repeat_checked": True,
        "source_family": "One municipal tree inventory. Group split by street, not independent cities.",
        "existing_private_bodies_inspected": False, "qualification_performed": False}
    write_new(TASK / "tests/receipts/geojson-authoring.json", encode(public_receipt))
    print(json.dumps({"public_features": len(public_features), "new_private_authored": True,
                      "group_disjointness": "passed", "complete_pristine_parity": "passed",
                      "private_membership_and_references_emitted": False}))


if __name__ == "__main__":
    main()
