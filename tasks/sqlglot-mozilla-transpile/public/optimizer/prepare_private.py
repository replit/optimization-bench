"""Blind private optimizer authoring. Never regenerate original private bodies.

Use only a trusted authoring environment. The console reports aggregates.
Detailed membership, parser diagnostics and complete results remain private.
"""
import argparse
import contextlib
import json
from pathlib import Path
import sys
import traceback

# Trusted authoring helpers, located explicitly even under Python -I.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import TASK, baseline, dump, sha

OLD_DIAGNOSTIC_ARCHIVE_SHA = "7de5c19c2b434506972ee2aecf94851783050cb642fdf32f449bb3cb14b8e3d0"


def template(tree):
    from sqlglot import exp
    def normalize(node):
        if isinstance(node, exp.Identifier):
            return exp.to_identifier("identifier")
        if isinstance(node, exp.Literal):
            return exp.Literal.string("string") if node.is_string else exp.Literal.number(0)
        return node
    return tree.transform(normalize).sql(comments=False)


def freeze(cases, detailed, provenance, args):
    tests = TASK / "tests"
    manifest_path = tests / "heldout/manifest.json"
    lock_path = tests / "heldout.lock.json"
    payload_path = TASK / "payload.json"
    provenance_path = tests / "heldout/optimizer-provenance.json"
    manifest = json.loads(manifest_path.read_text())
    original = [doc for batches in manifest["splits"].values() for batch in batches
                for doc in batch["docs"] if doc.get("family", "transpile") == "transpile"]
    current = [doc for batches in manifest["splits"].values() for batch in batches
               for doc in batch["docs"] if doc.get("family") == "optimizer"]
    lock = json.loads(lock_path.read_text())
    payload = json.loads(payload_path.read_text())
    previous = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    pending, retired = {}, set()

    def plan(path, raw, metadata=False):
        if path.exists():
            if path.read_bytes() != raw and not (metadata and args.freeze):
                raise ValueError("Frozen private artifact differs")
        elif not args.freeze:
            raise ValueError("Missing frozen private artifact")
        pending[path] = raw

    descriptors, body_records = [], []
    for index, (raw, expected) in enumerate(cases):
        digest, expected_digest = sha(raw), sha(expected)
        descriptors.append({
            "path": f"optimizer-private-{index:04d}", "family": "optimizer",
            "sha256": digest, "bytes": len(raw), "expect_len": len(expected), "expect_sha": expected_digest,
        })
        for relative, body in (
                ("heldout/files/" + digest, raw),
                ("heldout/expected/optimizer/" + expected_digest, expected)):
            plan(tests / relative, body)
            lock["files"][relative] = sha(body)
            body_records.append({
                "scope": "heldout", "name": Path(relative).name,
                "destination": "tests/" + relative, "bytes": len(body), "sha256": sha(body),
            })
    replacing = bool(current and current != descriptors)
    if replacing:
        # The approved replacement is exactly the prior one-case diagnostic,
        # never an arbitrary private collection or an original conversion case.
        if not (
            args.freeze and args.replace_diagnostic and len(current) == 1
            and previous and previous["format"] == "sqlglot-private-optimizer-v1"
            and previous["source_archive"]["sha256"] == OLD_DIAGNOSTIC_ARCHIVE_SHA
            and len(previous["members"]) == 1
            and previous["members"][0]["request_sha256"] == current[0]["sha256"]
        ):
            raise ValueError("Frozen private optimizer commitments differ")
        old = current[0]
        if any(old["sha256"] == doc["sha256"] or old["expect_sha"] == doc["expect_sha"]
               for doc in original):
            raise ValueError("Cannot replace an original conversion commitment")
        retired = {"heldout/files/" + old["sha256"],
                   "heldout/expected/optimizer/" + old["expect_sha"]}
        if any(tests / path in pending for path in retired):
            raise ValueError("Retired and new optimizer payload overlap")
        for path in retired:
            del lock["files"][path]
        payload["files"] = [row for row in payload["files"]
                            if row["destination"] not in {"tests/" + path for path in retired}]
        for split, batches in manifest["splits"].items():
            retained = []
            for batch in batches:
                docs = [doc for doc in batch["docs"] if doc.get("family") != "optimizer"]
                if docs:
                    retained.append({**batch, "docs": docs})
            manifest["splits"][split] = retained
    if not current or replacing:
        if not args.freeze:
            raise ValueError("Private optimizer family not frozen")
        manifest["splits"][next(iter(manifest["splits"]))].append({"docs": descriptors})
    after_original = [doc for batches in manifest["splits"].values() for batch in batches
                      for doc in batch["docs"] if doc.get("family", "transpile") == "transpile"]
    if after_original != original:
        raise ValueError("Original private commitments changed")
    if previous and not replacing:
        if detailed != previous["members"] or provenance["source_archive"] != previous["source_archive"]:
            raise ValueError("Existing private source commitments differ")
    provenance_raw = dump(provenance)
    plan(provenance_path, provenance_raw, metadata=True)
    lock["files"]["heldout/optimizer-provenance.json"] = sha(provenance_raw)
    manifest_raw = dump(manifest)
    lock["files"]["heldout/manifest.json"] = sha(manifest_raw)
    by_path = {row["destination"]: row for row in payload["files"]}
    for row in body_records:
        old = by_path.get(row["destination"])
        if old is None:
            payload["files"].append(row)
        elif old != row:
            raise ValueError("Private payload commitment differs")
    for row in payload["files"]:
        if row["destination"] == "tests/heldout/manifest.json":
            row["bytes"], row["sha256"] = len(manifest_raw), sha(manifest_raw)
    plan(manifest_path, manifest_raw, metadata=True)
    plan(payload_path, dump(payload), metadata=True)
    plan(lock_path, dump(lock), metadata=True)
    if args.freeze:
        # Check every relationship before writing. Publish the lock last.
        for path, raw in pending.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            staged = path.with_name(path.name + ".authoring-tmp")
            staged.write_bytes(raw)
            staged.replace(path)
        for relative in retired:
            (tests / relative).unlink()
    folder = args.workdir / "new-only-inputs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files").mkdir(exist_ok=True)
    for raw, _ in cases:
        (folder / "files" / sha(raw)).write_bytes(raw)
    (folder / "manifest.json").write_bytes(dump({
        "format": manifest["format"], "task": manifest["task"],
        "edition": "new private optimizer diagnostics only",
        "splits": {"private": [{"docs": descriptors}]},
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--replace-diagnostic", action="store_true",
                        help="Replace only the explicitly approved prior one-case SQLMesh diagnostic.")
    parser.add_argument("--expected-new-cases", type=Path,
                        help="Private probe commitments to authenticate before first adoption.")
    args = parser.parse_args()
    if args.replace_diagnostic and not args.freeze:
        parser.error("--replace-diagnostic requires --freeze")
    if args.replace_diagnostic and not args.expected_new_cases:
        parser.error("--replace-diagnostic requires --expected-new-cases")
    args.workdir.mkdir(parents=True, exist_ok=True)
    args.workdir.chmod(0o700)
    failed = False
    aggregate = {"status": "not started"}
    with (args.workdir / "private-authoring-details.log").open("w") as private_log:
        with contextlib.redirect_stdout(private_log), contextlib.redirect_stderr(private_log):
            try:
                optimizer = baseline(args.runtime)
                from mozilla_private import prepare
                cases, detailed, aggregate, provenance = prepare(args.archive, optimizer, template)
                if not cases:
                    raise ValueError("No eligible private inputs")
                if args.expected_new_cases:
                    expected = json.loads(args.expected_new_cases.read_text())
                    if [(sha(raw), sha(result)) for raw, result in cases] != [
                        (row["request_sha256"], row["output_sha256"]) for row in expected
                    ]:
                        raise ValueError("Reviewed probe commitments differ")
                freeze(cases, detailed, provenance, args)
                aggregate.update(new_private_references="frozen" if args.freeze else "verified",
                                 qualification="not performed")
            except Exception as error:
                traceback.print_exc()
                failed = True
                aggregate.update(status="authoring failed", error_class=type(error).__name__)
    (args.workdir / "aggregate.json").write_bytes(dump(aggregate))
    print(json.dumps(aggregate))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
