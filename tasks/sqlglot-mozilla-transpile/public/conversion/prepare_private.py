"""Owner-review expansion of an existing private unit, with immutable originals."""
import argparse
from collections import Counter
import contextlib
import copy
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tarfile
import traceback
import warnings

TASK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TASK / "public/optimizer"))
from prepare import baseline, dump, sha
spec = importlib.util.spec_from_file_location("optimizer_authoring", TASK / "public/optimizer/prepare_private.py")
optimizer_authoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer_authoring)
template = optimizer_authoring.template
PREFIX = "transpile-private-extra-"


def fingerprint(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def original_manifest(manifest):
    result = copy.deepcopy(manifest)
    for split, batches in result["splits"].items():
        result["splits"][split] = [
            {**batch, "docs": [doc for doc in batch["docs"] if not doc["path"].startswith(PREFIX)]}
            for batch in batches if any(not doc["path"].startswith(PREFIX) for doc in batch["docs"])]
    return result


def authenticate(task):
    payload = json.loads((task / "payload.json").read_text())
    lock = json.loads((task / "tests/heldout.lock.json").read_text())
    anchors = json.loads((task / "public/conversion/inherited.lock.json").read_text())
    private = task / "tests/heldout"
    if private.is_symlink() or any(path.is_symlink() for path in private.rglob("*")):
        raise ValueError("Private symlinks are not permitted")
    actual = {path.relative_to(task / "tests").as_posix() for path in private.rglob("*") if path.is_file()}
    if actual != set(lock["files"]):
        raise ValueError("Private commitment inventory changed")
    for relative, digest in lock["files"].items():
        path = task / "tests" / relative
        if path.is_symlink() or not path.resolve().is_relative_to(private.resolve()) or sha(path.read_bytes()) != digest:
            raise ValueError("Private commitment changed")
    for relative, digest in anchors["manifests"].items():
        raw = (task / relative).read_bytes()
        value = json.loads(raw)
        if sha(dump(original_manifest(value) if "splits" in value else value)) != digest:
            raise ValueError("Original manifest changed")
        entries = [row for row in payload["files"] if row["destination"] == relative]
        if relative.startswith("tests/") and len(entries) != 1:
            raise ValueError("Private manifest commitment missing")
        if any((row["bytes"], row["sha256"]) != (len(raw), sha(raw)) for row in entries):
            raise ValueError("Manifest payload commitment changed")
    manifest = json.loads((private / "manifest.json").read_text())
    additions = [doc for batches in manifest["splits"].values() for batch in batches
                 for doc in batch["docs"] if doc["path"].startswith(PREFIX)]
    excluded = {"heldout/manifest.json", "heldout/conversion-expansion-provenance.json"}
    for doc in additions:
        excluded.update((f"heldout/files/{doc['sha256']}",
                         f"heldout/expected/conversion-expansion/{doc['expect_sha']}"))
    original_files = {key: value for key, value in lock["files"].items() if key not in excluded}
    original_payload = sorted((row for row in payload["files"] if row["destination"] not in
                               {"tests/" + relative for relative in excluded}), key=lambda row: row["destination"])
    if fingerprint(original_files) != anchors["private_files_sha256"]:
        raise ValueError("Original private commitments changed")
    if fingerprint(original_payload) != anchors["payload_entries_sha256"]:
        raise ValueError("Original payload entries changed")
    return payload, lock


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def select(archive):
    import sqlglot
    from sqlglot import exp
    source = json.loads((TASK / "public/conversion/source.lock.json").read_text())
    assert (len(archive.read_bytes()), sha(archive.read_bytes())) == (source["bytes"], source["sha256"])
    with tarfile.open(archive) as tar:
        files = {member.name.split("/", 1)[1]: tar.extractfile(member).read()
                 for member in tar.getmembers() if member.isfile() and "/" in member.name}
    assert files["LICENSE"] == (TASK / "public/dev/LICENSE.mozilla").read_bytes()
    public = json.loads((TASK / "public/dev/manifest.json").read_text())["cases"]
    private = json.loads((TASK / "tests/heldout/original-manifest.json").read_text())["cases"]
    public_datasets = {row["source_path"].split("/")[2] for row in public}
    shapes, hashes = set(), set()
    for row in public + private:
        raw = files[row["source_path"]]
        assert sha(raw) == row["sha256"]
        hashes.add(sha(raw))
        for tree in sqlglot.parse(raw.decode(), read="bigquery", error_level=sqlglot.ErrorLevel.RAISE):
            if tree is not None:
                shapes.add(template(tree))
    selected, counts = [], Counter()
    logger = logging.getLogger("sqlglot")
    for path in sorted(files):
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != "sql" or not path.endswith("/view.sql") or parts[2] in public_datasets:
            continue
        counts["candidate_views"] += 1
        raw = files[path]
        if sha(raw) in hashes or any(marker in raw for marker in (b"{{", b"{%", b"${")):
            counts["duplicate_or_template"] += 1
            continue
        capture = Capture()
        logger.addHandler(capture)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                trees = sqlglot.parse(raw.decode(), read="bigquery", error_level=sqlglot.ErrorLevel.RAISE)
                if len(trees) != 1 or not isinstance(trees[0], (exp.Query, exp.Create)):
                    counts["not_query_or_view_definition"] += 1
                    continue
                candidate_shapes = {template(trees[0])}
                if isinstance(trees[0], exp.Create):
                    if str(trees[0].args.get("kind", "")).upper() != "VIEW" or not isinstance(trees[0].expression, exp.Query):
                        counts["not_view_definition"] += 1
                        continue
                    candidate_shapes.add(template(trees[0].expression))
                if candidate_shapes & shapes:
                    counts["existing_template"] += 1
                    continue
                output = sqlglot.transpile(raw.decode(), read="bigquery", write="duckdb", pretty=False,
                                          error_level=sqlglot.ErrorLevel.RAISE,
                                          unsupported_level=sqlglot.ErrorLevel.RAISE)
                assert type(output) is list and len(output) == 1 and type(output[0]) is str
                sqlglot.parse_one(output[0], read="duckdb", error_level=sqlglot.ErrorLevel.RAISE)
                if capture.records or caught:
                    counts["warnings"] += 1
                    continue
                expected = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()
                selected.append({"source_path": path, "raw": raw, "expected": expected})
                shapes.update(candidate_shapes)
                hashes.add(sha(raw))
        except (sqlglot.errors.SqlglotError, UnicodeError) as error:
            counts["unsupported:" + type(error).__name__] += 1
        finally:
            logger.removeHandler(capture)
    assert selected
    return selected, {"source": source, "selection_counts": dict(counts),
                      "rule": "All compatible distinct views outside public datasets and original statement templates. Original query.sql exclusions remain untouched. No timing selection."}


def freeze(selected, provenance, enabled):
    payload, lock = authenticate(TASK)
    path = TASK / "tests/heldout/manifest.json"
    manifest = json.loads(path.read_text())
    old = [doc for groups in manifest["splits"].values() for group in groups for doc in group["docs"]
           if doc["path"].startswith(PREFIX)]
    writes, docs, members = {}, [], []

    def plan(path, raw, metadata=False):
        if path.is_symlink() or not path.resolve().is_relative_to(TASK.resolve()):
            raise ValueError("Artifact path escaped task")
        if path.exists() and path.read_bytes() == raw:
            return
        if not enabled or (path.exists() and not metadata):
            raise ValueError("Cannot replace or omit a frozen artifact")
        writes[path] = raw

    for index, row in enumerate(selected):
        raw, expected = row["raw"], row["expected"]
        docs.append({"path": f"{PREFIX}{index:04d}", "family": "transpile",
                     "sha256": sha(raw), "bytes": len(raw),
                     "expect_sha": sha(expected), "expect_len": len(expected)})
        members.append({"source_path": row["source_path"], "input_sha256": sha(raw), "output_sha256": sha(expected)})
        for relative, body in [(f"heldout/files/{sha(raw)}", raw),
                               (f"heldout/expected/conversion-expansion/{sha(expected)}", expected)]:
            plan(TASK / "tests" / relative, body)
            entry = {"scope": "heldout", "name": Path(relative).name, "destination": "tests/" + relative,
                     "bytes": len(body), "sha256": sha(body)}
            current = next((item for item in payload["files"] if item["destination"] == entry["destination"]), None)
            if current is None:
                if not enabled:
                    raise ValueError("Payload commitment missing")
                payload["files"].append(entry)
            elif any(current.get(key) != value for key, value in entry.items()):
                raise ValueError("Payload commitment changed")
            lock["files"][relative] = sha(body)
    if old:
        if old != docs:
            raise ValueError("Cannot replace the frozen expansion")
    elif enabled:
        manifest["splits"][next(iter(manifest["splits"]))].append({"docs": docs})
    else:
        raise ValueError("Expansion has not been frozen")
    manifest_raw = dump(manifest)
    plan(path, manifest_raw, metadata=True)
    lock["files"]["heldout/manifest.json"] = sha(manifest_raw)
    entry, = [item for item in payload["files"] if item["destination"] == "tests/heldout/manifest.json"]
    entry.update(bytes=len(manifest_raw), sha256=sha(manifest_raw))
    raw = dump({**provenance, "members": members, "requires_task_owner_approval": True})
    relative = "heldout/conversion-expansion-provenance.json"
    plan(TASK / "tests" / relative, raw)
    lock["files"][relative] = sha(raw)
    plan(TASK / "payload.json", dump(payload), metadata=True)
    plan(TASK / "tests/heldout.lock.json", dump(lock), metadata=True)
    for path, raw in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".authoring-tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
    return {"added_views": len(docs), "freeze": enabled, "original_commitments_preserved": True}


def main(args):
    if os.environ.get("PYTHONHASHSEED") != "0" or sys.flags.ignore_environment or sys.flags.hash_randomization:
        raise ValueError("Require startup PYTHONHASHSEED=0 and non-isolated Python")
    authenticate(TASK)
    baseline(args.runtime)
    selected, provenance = select(args.archive)
    return freeze(selected, provenance, args.freeze)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    args.diagnostics.mkdir(parents=True)
    args.diagnostics.chmod(0o700)
    try:
        with (args.diagnostics / "details.log").open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = main(args)
    except Exception:
        with (args.diagnostics / "details.log").open("a") as log:
            traceback.print_exc(file=log)
        raise SystemExit("Private authoring failed, diagnostics retained privately")
    (args.diagnostics / "aggregate.json").write_bytes(dump(result))
    print(json.dumps(result))
