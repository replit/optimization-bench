"""Freeze reviewed source packets and complete results, without replacing old data.

Packets and source provenance are trusted authoring inputs, not candidate output.
Every existing relationship is checked before any write. Repeated adoption is
read-only verification. Private diagnostics and membership never reach stdout.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

TASK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TASK / "public/optimizer"))
from prepare import baseline, derived_commitments, dump, sha


def plan(path, raw, freeze, writes, metadata=False):
    if path.exists():
        if path.read_bytes() != raw:
            if not (metadata and freeze):
                raise ValueError("Frozen artifact differs")
            writes[path] = raw
    elif freeze:
        writes[path] = raw
    else:
        raise ValueError("Frozen artifact is missing")


def inherited_manifest(manifest):
    result = copy.deepcopy(manifest)
    for split, batches in result["splits"].items():
        retained = []
        for batch in batches:
            batch["docs"] = [doc for doc in batch["docs"] if doc.get("family") not in ("lineage", "diff")]
            if batch["docs"]:
                retained.append(batch)
        result["splits"][split] = retained
    return result


def authenticate_existing(task, payload, heldout_lock):
    # Public manifests are Git metadata, not payload inventory members. This
    # fixed parent-revision anchor authenticates their inherited contents too.
    anchors = json.loads((task / "public/alternates/inherited.lock.json").read_text())
    private_root = task / "tests/heldout"
    actual = {path.relative_to(task / "tests").as_posix() for path in private_root.rglob("*") if path.is_file()}
    if actual != set(heldout_lock["files"]):
        raise ValueError("Private artifact commitment inventory changed")
    for relative, digest in heldout_lock["files"].items():
        path = task / "tests" / relative
        if path.is_symlink() or not path.resolve().is_relative_to(private_root.resolve()):
            raise ValueError("Invalid private artifact commitment path")
        if sha(path.read_bytes()) != digest:
            raise ValueError("Existing private artifact commitment changed")
    for relative, anchor in anchors["manifests"].items():
        raw = (task / relative).read_bytes()
        if sha(dump(inherited_manifest(json.loads(raw)))) != anchor["canonical_sha256"]:
            raise ValueError("Inherited manifest commitment changed")
        entries = [row for row in payload["files"] if row["destination"] == relative]
        if relative.startswith("tests/") and len(entries) != 1:
            raise ValueError("Private manifest payload commitment missing")
        for entry in entries:
            if (entry["bytes"], entry["sha256"]) != (len(raw), sha(raw)):
                raise ValueError("Existing manifest payload commitment changed")


def adopt(args):
    if os.environ.get("PYTHONHASHSEED") != "0" or sys.flags.ignore_environment or sys.flags.hash_randomization:
        raise ValueError("Authoring requires PYTHONHASHSEED=0 and a non-isolated interpreter")
    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    heldout_lock_path = TASK / "tests/heldout.lock.json"
    heldout_lock = json.loads(heldout_lock_path.read_text())
    authenticate_existing(TASK, payload, heldout_lock)
    baseline(args.runtime)
    sys.path.insert(0, str(TASK / "environment/workload/driver"))
    from analysis_lineage import encode, run_operation
    from analysis_diff import run
    inputs = {
        ("public", "lineage"): args.packet / "lineage-public-inputs.json",
        ("private", "lineage"): args.packet / "private/lineage-inputs.json",
        ("public", "diff"): args.packet / "diff-public-inputs.json",
        ("private", "diff"): args.packet / "private/diff-expanded-inputs.json",
    }
    lock_packet = json.loads(args.packet_lock.read_text())
    expected_packet_paths = {path.relative_to(args.packet).as_posix() for path in inputs.values()} | {
        "private/lineage-provenance.json", "private/diff-expanded-provenance.json"}
    if set(lock_packet) != expected_packet_paths:
        raise ValueError("Unexpected source packet inventory")
    for relative in sorted(expected_packet_paths):
        if sha((args.packet / relative).read_bytes()) != lock_packet[relative]:
            raise ValueError("Reviewed input packet changed")
    writes, counts = {}, {}
    references = {}
    derived = derived_commitments()
    for scope in ("public", "private"):
        root = TASK / ("environment/workload/inputs" if scope == "public" else "tests/heldout")
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        old_docs = [doc for batches in manifest["splits"].values() for batch in batches for doc in batch["docs"]]
        preserved = [doc for doc in old_docs if doc.get("family") not in ("lineage", "diff")]
        for family, operation in (("lineage", run_operation), ("diff", run)):
            cases = json.loads(inputs[scope, family].read_text())["cases"]
            identities = {json.dumps({k: v for k, v in case.items() if k != "id"}, sort_keys=True) for case in cases}
            if len(identities) != len(cases):
                raise ValueError("Repeated request")
            descriptors, records = [], []
            for index, case in enumerate(cases):
                raw, expected = encode(case), operation([case])
                result = json.loads(expected)["results"][0]
                if result["status"] != "ok" or result["logs"] or result["python_warnings"]:
                    raise ValueError("Unsupported baseline result")
                if family == "lineage" and not result["graph"]["roots"]:
                    raise ValueError("Empty lineage graph")
                if family == "diff" and not any(
                        not edit["kind"].endswith(".Keep") for edit in result["comparisons"][0]["edits"]):
                    raise ValueError("Unchanged diff pair")
                digest, expected_digest = sha(raw), sha(expected)
                descriptors.append({"path": f"{family}-{scope}-{index:04d}", "family": family,
                                    "sha256": digest, "bytes": len(raw),
                                    "expect_len": len(expected), "expect_sha": expected_digest})
                records.append({"input_sha256": digest, "output_sha256": expected_digest,
                                "output_bytes": len(expected)})
                path = root / "files" / digest
                plan(path, raw, args.freeze, writes)
                body_rows = [(path, raw)]
                if scope == "private":
                    expected_path = root / "expected" / family / expected_digest
                    plan(expected_path, expected, args.freeze, writes)
                    body_rows.append((expected_path, expected))
                for path, body in body_rows:
                    row = {"scope": "public" if scope == "public" else "heldout",
                           "name": path.name, "destination": path.relative_to(TASK).as_posix(),
                           "bytes": len(body), "sha256": sha(body)}
                    if scope == "public":
                        row["distribution"] = "git"
                        if derived.get(row["destination"]) == (row["bytes"], row["sha256"]):
                            continue  # TPC-derived: committed by the public input packet instead.
                    existing = next((old for old in payload["files"] if old["destination"] == row["destination"]), None)
                    if existing is None:
                        if not args.freeze:
                            raise ValueError("Payload commitment missing")
                        payload["files"].append(row)
                    elif any(existing.get(key) != value for key, value in row.items()):
                        raise ValueError("Payload commitment changed")
                    if scope == "private":
                        relative = path.relative_to(TASK / "tests").as_posix()
                        if relative in heldout_lock["files"] and heldout_lock["files"][relative] != sha(body):
                            raise ValueError("Private commitment changed")
                        heldout_lock["files"][relative] = sha(body)
            current = [doc for doc in old_docs if doc.get("family") == family]
            if current:
                if current != descriptors:
                    raise ValueError("Cannot replace a frozen family")
            elif args.freeze:
                manifest["splits"][next(iter(manifest["splits"]))].append({"docs": descriptors})
            else:
                raise ValueError("Family has not been frozen")
            counts[scope + "_" + family] = len(cases)
            references[scope + "/" + family] = records
        after = [doc for batches in manifest["splits"].values() for batch in batches for doc in batch["docs"]
                 if doc.get("family") not in ("lineage", "diff")]
        if after != preserved:
            raise ValueError("Existing workload changed")
        raw_manifest = dump(manifest)
        plan(manifest_path, raw_manifest, args.freeze, writes, metadata=True)
        if scope == "private":
            heldout_lock["files"]["heldout/manifest.json"] = sha(raw_manifest)
        for row in payload["files"]:
            if row["destination"] == manifest_path.relative_to(TASK).as_posix():
                row["bytes"], row["sha256"] = len(raw_manifest), sha(raw_manifest)
    private_provenance = {
        "format": "sqlglot-alternates-private-v1",
        "lineage": json.loads((args.packet / "private/lineage-provenance.json").read_text()),
        "diff": json.loads((args.packet / "private/diff-expanded-provenance.json").read_text()),
        "commitments": {key: value for key, value in references.items() if key.startswith("private/")},
    }
    private_path = TASK / "tests/heldout/alternates-provenance.json"
    private_raw = dump(private_provenance)
    plan(private_path, private_raw, args.freeze, writes)
    heldout_lock["files"]["heldout/alternates-provenance.json"] = sha(private_raw)
    public_provenance = {
        "format": "sqlglot-alternates-public-v1",
        "lineage_sources": json.loads((TASK / "public/optimizer/provenance.json").read_text()),
        "diff_source": {"repository": "https://github.com/tobymao/sqlglot",
                        "commit": "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76",
                        "file": "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql",
                        "sha256": "f584cd3a0dac63ca9654f74ef53011f4b9db7cad223f812de621dadb2ff982d4"},
        "commitments": {key: value for key, value in references.items() if key.startswith("public/")},
        "source_rule": "Public SQLGlot/SQLMesh and private Mozilla, separately within each family. Sources may recur across families.",
        "result_rule": "Complete reviewed graph/edit contracts. Per-request envelopes preserve every result field.",
    }
    plan(TASK / "public/alternates/provenance.json", dump(public_provenance), args.freeze, writes)
    plan(payload_path, dump(payload), args.freeze, writes, metadata=True)
    subset = {"schema_version": 1, "task": payload["task"], "files": [
        {**row, "destination": row["destination"].removeprefix("environment/")}
        for row in payload["files"] if row["scope"] == "public"]}
    plan(TASK / "environment/payload-public.json", dump(subset), args.freeze, writes, metadata=True)
    plan(heldout_lock_path, dump(heldout_lock), args.freeze, writes, metadata=True)
    if args.freeze:
        for path, raw in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".authoring-tmp")
            temporary.write_bytes(raw)
            temporary.replace(path)
    return {"cases": counts, "existing_workloads_preserved": True, "freeze": args.freeze}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--packet-lock", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    args.diagnostics.mkdir(parents=True)
    args.diagnostics.chmod(0o700)
    try:
        with (args.diagnostics / "details.log").open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = adopt(args)
    except Exception:
        with (args.diagnostics / "details.log").open("a") as log:
            traceback.print_exc(file=log)
        raise SystemExit("Adoption failed, diagnostics retained privately")
    (args.diagnostics / "aggregate.json").write_bytes(dump(result))
    print(json.dumps(result))
