"""Authenticate a reviewed retained-data packet into a fresh verifier-only Git view."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from judge import heldout
from tools import export_source_snapshot, stage_payload
from tools.payload_check import Fetcher

SERDE = "serde-json-production-crate-index-ingestion"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(name):
    if not isinstance(name, str):
        raise ValueError("Packet path must be a string")
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or ".." in path.parts or "\\" in name or ".git" in path.parts
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError("Packet path must be an exact contained task-relative path: " + name)
    return path


def inventory(task):
    task = Path(task).resolve()
    lock_path, payload_path = task / "tests/heldout.lock.json", task / "payload.json"
    heldout.reject_lfs_pointer(lock_path)
    lock = json.loads(lock_path.read_text())
    if lock["schema_version"] != 1 or lock["status"] != "available":
        raise ValueError("Original held-out lock is unavailable; restore its reviewed source contract, not new commitments")
    payload = json.loads(payload_path.read_text())
    references = heldout.reference_payload(heldout.references(lock, task / "tests"), payload)
    retained = {}
    source = {}
    for name, digest in lock["files"].items():
        path = "tests/" + relative(name).as_posix()
        target = retained if path.startswith("tests/heldout/") else source
        target[path] = {"path": path, "sha256": digest, "bytes": None}
    for entry in payload["files"]:
        if entry["scope"] != "heldout" or entry["destination"] in references:
            continue
        name = relative(entry["destination"]).as_posix()
        if entry.get("kind", "file") not in ("file", "repo-lfs"):
            raise ValueError("Retained directory/source acquisition is not a file packet: " + name)
        if not isinstance(entry.get("sha256"), str) or type(entry.get("bytes")) is not int:
            raise ValueError("Original retained payload size/hash is unresolved: " + name)
        target = retained if name.startswith("tests/heldout/") else source
        if name not in target or target[name]["sha256"] != entry["sha256"]:
            raise ValueError("Retained payload differs from the original available lock: " + name)
        target[name]["bytes"] = entry["bytes"]
    for row in source.values():
        path = stage_payload.contained(task, row["path"])
        if not path.is_file() or sha(path) != row["sha256"]:
            raise ValueError("Required protected source is missing/corrupt: " + row["path"]
                             + "; recover original code separately, never from a data packet")
        row["bytes"] = path.stat().st_size
    return {"task": task.name, "heldout_lock_sha256": sha(lock_path), "payload_sha256": sha(payload_path),
            "files": sorted(retained.values(), key=lambda row: row["path"]),
            "source_required_files": sorted(source.values(), key=lambda row: row["path"]),
            "fetch_only_input_references_excluded": len(references),
            "rights_or_publication_clearance": "not-evaluated"}


def validate_metadata(expected, metadata):
    if not isinstance(metadata, dict) or not isinstance(metadata.get("files"), list):
        raise ValueError("Reviewed packet metadata requires its complete original file inventory")
    for key in ("task", "heldout_lock_sha256", "payload_sha256"):
        if metadata.get(key) != expected[key]:
            raise ValueError("Reviewed packet metadata does not match frozen source " + key)
    if not isinstance(metadata.get("rights_basis"), str) or not metadata["rights_basis"].strip():
        raise ValueError("Reviewed per-source rights/notices basis is required; owner release approval alone is insufficient")
    declared = {row["path"]: row for row in expected["files"]}
    files = {}
    for row in metadata["files"]:
        if not isinstance(row, dict):
            raise ValueError("Reviewed packet file records must name original paths, bytes and SHA-256")
        name = relative(row.get("path")).as_posix()
        if name in files or name not in declared:
            raise ValueError("Duplicate/extra packet metadata member, or protected-code overwrite: " + name)
        original = declared[name]
        if (not re.fullmatch("[a-f0-9]{64}", str(row.get("sha256", "")))
                or row["sha256"] != original["sha256"] or type(row.get("bytes")) is not int or row["bytes"] < 0
                or original["bytes"] is not None and row["bytes"] != original["bytes"]):
            raise ValueError("Packet member size/hash differs from frozen declarations: " + name)
        files[name] = {"path": name, "bytes": row["bytes"], "sha256": row["sha256"]}
    if files.keys() != declared.keys():
        raise ValueError("Reviewed packet metadata is missing required retained assets: "
                         + ", ".join(sorted(declared.keys() - files.keys())))
    record = metadata.get("archive")
    if (not isinstance(record, dict) or type(record.get("bytes")) is not int or record["bytes"] <= 0
            or not re.fullmatch("[a-f0-9]{64}", str(record.get("sha256", "")))):
        raise ValueError("Reviewed archive requires original compressed bytes and SHA-256")
    if record.get("url"):
        if not isinstance(record["url"], str):
            raise ValueError("Reviewed archive URL must be a stable public HTTPS string")
        url = urlsplit(record["url"])
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.port not in (None, 443) or url.query or url.fragment
                or "\\" in record["url"] or any(ord(char) <= 32 for char in record["url"])):
            raise ValueError("Use a stable credential-free public HTTPS release URL without query/fragment, or a local packet")
    return files, record


def unpack(packet, destination, files):
    directories = {parent.as_posix() for name in files
                   for parent in PurePosixPath(name).parents if parent.parts}
    seen, restored = set(), set()
    with tarfile.open(packet, mode="r|gz") as archive:
        for member in archive:
            name = relative(member.name).as_posix()
            if name in seen:
                raise ValueError("Duplicate packet archive member: " + name)
            seen.add(name)
            if member.isdir():
                if name not in directories or member.size:
                    raise ValueError("Undeclared packet directory: " + name)
                continue
            if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                raise ValueError("Packet permits regular files only, never links/devices: " + name)
            if name not in files:
                raise ValueError("Extra packet member or code/guard/lock overwrite: " + name)
            row = files[name]
            if member.size != row["bytes"]:
                raise ValueError("Packet expansion differs from original declared size: " + name)
            target = stage_payload.contained(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream, length=1024 * 1024)
            heldout.reject_lfs_pointer(target)
            if target.stat().st_size != row["bytes"] or sha(target) != row["sha256"]:
                raise ValueError("Corrupt original packet asset: " + name)
            restored.add(name)
    if restored != files.keys():
        raise ValueError("Packet is missing required retained assets: " + ", ".join(sorted(files.keys() - restored)))


def prepare(task, metadata, output, archive=None):
    if Path(output).is_symlink():
        raise ValueError("Choose a new verifier-only output, not a symlink")
    task, output = Path(task).resolve(), Path(output).resolve()
    if task.name == SERDE:
        raise ValueError("Serde's restricted six goldens are not release-packet assets; use its seven-file "
                         "scorer-metadata bundle and fixed pristine-producer restorer, then the existing thirteen-file stage flow")
    expected = inventory(task)
    files, record = validate_metadata(expected, metadata)
    root = Path(subprocess.check_output(["git", "-C", str(task), "rev-parse", "--show-toplevel"],
                                        text=True).strip()).resolve()
    task_relative = task.relative_to(root)
    if output.exists() or output.is_symlink() or output.is_relative_to(root):
        raise ValueError("Choose a new verifier-only output outside the source checkout; no live view is overwritten")
    if archive is not None:
        archive = Path(archive).resolve()
        if archive.is_relative_to(root):
            raise ValueError("Keep retained-data packets outside ordinary Git/source checkouts")
    elif not record.get("url"):
        raise ValueError("No approved public archive URL exists in this metadata; supply its authenticated local --archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="verifier-packet-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        packet = archive if archive is not None else Fetcher(temporary / "download").url(record)
        if not packet.is_file() or packet.stat().st_size != record["bytes"] or sha(packet) != record["sha256"]:
            raise ValueError("Retained archive size/SHA-256 mismatch; obtain the exact reviewed original packet")
        originals = temporary / "originals"
        originals.mkdir()
        unpack(packet, originals, files)
        workspace = temporary / "checkout"
        sidecar = root / "scorer-metadata" / SERDE
        selection = {"scorer_tasks": [SERDE], "scorer_originals": sidecar} if sidecar.is_dir() else {}
        export_source_snapshot.export(root, workspace, **selection)
        view = workspace / task_relative
        with (workspace / ".git/info/exclude").open("a") as ignored:
            ignored.write("\n/" + task_relative.as_posix() + "/tests/heldout/\n/verifier-assets-prepared.json\n")
        for row in expected["source_required_files"]:
            path = stage_payload.contained(view, row["path"])
            if not path.is_file() or sha(path) != row["sha256"]:
                raise ValueError("Source export lacks required protected code: " + row["path"]
                                 + "; data packets cannot repair source/guard/lock closure")
        staged = stage_payload.stage(view, source=originals, scope="heldout")
        payload_paths = {entry["destination"] for entry in json.loads((view / "payload.json").read_text())["files"]
                         if entry["scope"] == "heldout" and entry.get("retention") != "fetch-only"}
        lock_only = files.keys() - payload_paths
        for name in lock_only:
            target = stage_payload.contained(view, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage_payload.contained(originals, name), target)
        if (sha(view / "tests/heldout.lock.json") != expected["heldout_lock_sha256"]
                or sha(view / "payload.json") != expected["payload_sha256"]):
            raise ValueError("Prepared source commitments changed; no verifier view is usable")
        # Canonical acquisition verifies retained bytes before its later, unchanged input fetches.
        checked = heldout.check(view / "tests", require_available=True, allow_missing_references=True)
        receipt = {"task": task.name, "prepared_task": str(output / task_relative),
                   "heldout_lock_sha256": sha(view / "tests/heldout.lock.json"),
                   "payload_sha256": sha(view / "payload.json"), "archive_bytes": record["bytes"],
                   "archive_sha256": record["sha256"], "retained_files": len(files),
                   "retained_bytes": sum(row["bytes"] for row in files.values()),
                   "payload_files_staged": staged, "lock_only_files_restored": len(lock_only),
                   "canonical_retained_check": checked["status"], "input_acquisition_performed": False,
                   "source_task_modified": False, "native_or_model_run": False,
                   "public_availability_or_qualification_proved": False}
        (workspace / "verifier-assets-prepared.json").write_text(json.dumps(receipt, indent=2) + "\n")
        workspace.rename(output)
    return receipt


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate reviewed metadata key: " + key)
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--inventory", action="store_true", help="Print required data/code roles without acquiring bodies")
    parser.add_argument("--metadata", type=Path, help="Reviewed release archive and original per-file size/hash metadata")
    parser.add_argument("--archive", type=Path, help="Already authorized local tar.gz; no URL or credentials needed")
    parser.add_argument("--out", type=Path, help="New verifier-only Git checkout, never the source or agent workspace")
    args = parser.parse_args(argv)
    try:
        if args.inventory:
            result = inventory(args.task)
        else:
            if args.metadata is None or args.out is None:
                parser.error("--metadata and --out are required for preparation")
            metadata = json.loads(args.metadata.read_text(), object_pairs_hook=unique_object)
            result = prepare(args.task, metadata, args.out, args.archive)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, EOFError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
        parser.exit(2, "Verifier assets unavailable: " + str(error) + "\n")


if __name__ == "__main__":
    main()
