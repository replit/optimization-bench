"""Export hydrated code into fresh Git without donor Git objects or remotes."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POINTER = b"version https://git-lfs.github.com/spec/v1\n"
SCORER_METADATA = {
    "serde-json-production-crate-index-ingestion": {
        "tests/heldout/manifest.json": (28357921, "705b6e5cc6546c246ba2a6c76fb8d122a3471af4b08b24784eff8370cfb2701e"),
        "tests/heldout/references/license-policy.json": (435, "52b73c71298b3617cf8140af82e3fc2d69ea19b2a755f37b9c46d246cd12237d"),
        "tests/heldout/references/overlap-preflight.json": (4330, "c350f63470b0b3a2631402b54b5f716e88d4d9c5d13ab9c6469955cc131b65de"),
        "tests/heldout/references/reference-generation.json": (1411, "79e5b8a68c5427223b79d03f2caf6add79256b4ec9ca550f54bc47fa7310f738"),
        "tests/heldout/references/selection-freeze.json": (124767, "f9a264bf6619a5218493c49bbafd7b462904af1c364f9f23265a5aaa7190e2d0"),
        "tests/heldout/references/sources.json": (25504339, "03e97cb1f4fc64f6a9bb18d729e5e23970a632742b56d4a21ab34bd80d453b38"),
        "tests/heldout/references/geojson-typed-selection.json": (660, "264a097d97acbe7da2697661e9ceab91c5642ae85717e0db956f14178edbc557"),
    }
}


def body_paths(root):
    from tools.payload_check import repo_lfs_spec
    from judge import heldout, public_packet
    paths, tasks = set(), {}
    for task in sorted((root / "tasks").iterdir()):
        manifest = task / "payload.json"
        if not manifest.is_file():
            continue
        entries = json.loads(manifest.read_text())["files"]
        tasks[task.name] = (task, entries)
        for entry in entries:
            paths.add((task / entry["destination"]).relative_to(root).as_posix())
            if entry.get("kind") == "repo-lfs":
                paths.add((task / repo_lfs_spec(entry)).relative_to(root).as_posix())
        lock = task / "tests/heldout.lock.json"
        if lock.is_file():
            for name in heldout.references(json.loads(lock.read_text()), task / "tests"):
                paths.add((task / "tests" / name).relative_to(root).as_posix())
        packet = public_packet.load(task)
        if packet:
            paths.update((task / entry["destination"]).relative_to(root).as_posix()
                         for entry in packet["derived_inputs"])
    return paths, tasks


def under(name, paths):
    path = PurePosixPath(name)
    return any(parent.as_posix() in paths for parent in (path, *path.parents))


def reviewed_public_fixtures(root, tasks, inventories):
    from tools import stage_payload
    from tools.payload_check import repo_lfs_spec
    retained, receipts = {}, []
    for inventory in inventories:
        relative = Path(inventory)
        if relative.is_absolute():
            relative = relative.relative_to(root)
        manifest = stage_payload.contained(root, relative.as_posix())
        subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
                       cwd=root, check=True, stdout=subprocess.DEVNULL)
        value = json.loads(manifest.read_text())
        if value["task"] not in tasks:
            raise ValueError("Public fixture inventory names an absent task")
        task, entries = tasks[value["task"]]
        if value["task_root"] != task.relative_to(root).as_posix() or value["path_base"] != "task_root":
            raise ValueError("Public fixture inventory has an invalid task path boundary")
        inputs, notices = value["inputs"], value["required_notices"]
        if (not inputs or not notices or not value.get("rights_basis") or not value.get("notice_conditions")
                or value["count"] != len(inputs) or value["bytes"] != sum(row["bytes"] for row in inputs)):
            raise ValueError("Explicit reviewed inputs, notices, rights conditions and exact totals are required")
        public = {entry["destination"]: entry for entry in entries if entry["scope"] == "public"}
        selected = {}
        for row in inputs:
            entry = public.get(row.get("staged_destination", row["source_path"]))
            if (entry is None or entry["bytes"] != row["bytes"] or entry["sha256"] != row["sha256"]
                    or not row.get("source_license") or not row.get("source_license_url", "").startswith("https://")):
                raise ValueError("Retained fixture is not an explicitly licensed exact public payload entry")
            allowed = {entry["destination"]}
            if entry.get("kind") == "repo-lfs":
                allowed.add(repo_lfs_spec(entry))
            if row["source_path"] not in allowed:
                raise ValueError("Retained fixture source is not its declared public destination or recovery object")
            selected[row["source_path"]] = {"bytes": row["bytes"], "sha256": row["sha256"]}
        if len(selected) != len(inputs):
            raise ValueError("Duplicate retained public fixture")
        for notice in notices:
            if not notice["path"].startswith(("notices/", "environment/notices/")):
                raise ValueError("Retained license notice is outside the task notice directories")
            path = stage_payload.contained(task, notice["path"])
            selected[notice["path"]] = {"bytes": path.stat().st_size, "sha256": notice["sha256"]}
        for name, entry in selected.items():
            source = stage_payload.contained(task, name)
            if not source.is_file() or not stage_payload.authentic(source, entry):
                raise ValueError("Hydrate and authenticate the exact reviewed public fixture/notice: " + name)
            relative_name = source.relative_to(root).as_posix()
            if relative_name in retained:
                raise ValueError("Duplicate retained fixture inventory path")
            retained[relative_name] = entry
        receipts.append({"path": relative.as_posix(), "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                         "task": task.name, "fixture_count": len(inputs), "fixture_bytes": value["bytes"],
                         "notice_count": len(notices), "publication_clearance": "not-evaluated"})
    return retained, receipts


def scorer_metadata(tasks, names, originals):
    from tools import stage_payload
    retained, receipts = {}, []
    if originals is not None and len(names) != 1:
        raise ValueError("Select exactly one approved scorer-metadata task with --scorer-metadata-from")
    for name in names:
        if name not in SCORER_METADATA or name not in tasks:
            raise ValueError("No approved authored scorer-metadata policy/task: " + name)
        task, entries = tasks[name]
        payload = {entry["destination"]: entry for entry in entries}
        lock = json.loads((task / "tests/heldout.lock.json").read_text())
        bundle = "scorer-metadata/" + name
        total = 0
        for path, (size, digest) in SCORER_METADATA[name].items():
            relative = PurePosixPath(path)
            if (path != "tests/heldout/manifest.json" and
                    (relative.parent.as_posix() != "tests/heldout/references" or relative.suffix != ".json")):
                raise ValueError("Scorer bundle permits only approved authored metadata, not inputs or golden bodies")
            entry = payload.get(path)
            if (entry is None or entry.get("scope") != "heldout"
                    or entry.get("role") == "input" or entry.get("retention") == "fetch-only"
                    or entry.get("bytes") != size or entry.get("sha256") != digest
                    or lock["files"].get(relative.relative_to("tests").as_posix()) != digest):
                raise ValueError("Frozen scorer-metadata payload/lock pins differ: " + name + "/" + path)
            if size > 100 * 1024 * 1024:
                raise ValueError("Scorer metadata exceeds ordinary Git size; an approved external packaging route is required")
            source = stage_payload.source_candidate(Path(originals).resolve() if originals else task, entry)
            try:
                verified = source is not None and stage_payload.authentic(source, entry)
            except ValueError:
                verified = False
            if not verified:
                raise ValueError("Missing/corrupt original authored scorer metadata: " + name + "/" + path
                                 + "; supply authenticated originals with --scorer-metadata-from. "
                                 "No golden/input acquisition or replacement metadata is performed.")
            retained[bundle + "/" + path] = {"source": source, "bytes": size, "sha256": digest}
            total += size
        receipts.append({"task": name, "bundle_root": bundle, "files": len(SCORER_METADATA[name]),
                         "bytes": total, "staging_layout": "original payload entry.destination paths for --from",
                         "data_golden_or_input_bodies_included": False, "task_paths_modified": False,
                         "publication_clearance": "not-evaluated"})
    return retained, receipts


def export(root, output, public_tasks=(), retention_inventories=(), scorer_tasks=(), scorer_originals=None):
    from tools import stage_payload
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Choose a new snapshot directory outside the donor checkout")
    for arguments in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
        subprocess.run(["git", "-c", "core.trustctime=false", *arguments], cwd=root, check=True)
    identity = os.environ.copy()
    for role in ("AUTHOR", "COMMITTER"):
        configured = subprocess.check_output(["git", "var", "GIT_" + role + "_IDENT"],
                                             cwd=root, text=True, stderr=subprocess.PIPE).strip()
        matched = re.fullmatch(r"(.*) <([^<>]+)> \d+ [+-]\d{4}", configured)
        if matched is None:
            raise ValueError("Configure a valid Git identity before creating the fresh snapshot")
        identity["GIT_" + role + "_NAME"], identity["GIT_" + role + "_EMAIL"] = matched.groups()
    excluded, tasks = body_paths(root)
    retained, retention_receipts = reviewed_public_fixtures(root, tasks, retention_inventories)
    metadata, metadata_receipts = scorer_metadata(tasks, scorer_tasks, scorer_originals)
    retained.update(metadata)
    if set(public_tasks) - tasks.keys():
        raise ValueError("Unknown selected public-input task")
    output.parent.mkdir(parents=True, exist_ok=True)
    count = hydrated = 0
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        destination = Path(temporary) / "source"
        destination.mkdir()
        with subprocess.Popen(["git", "archive", "--format=tar", "HEAD"], cwd=root,
                              stdout=subprocess.PIPE) as process:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    name = member.name.rstrip("/")
                    if (name == ".lfsconfig" or name == "scorer-metadata"
                            or name.startswith("scorer-metadata/") or under(name, excluded)
                            or name.startswith("judge/hosts/") and
                            name not in ("judge/hosts/registry.schema.json", "judge/hosts/qualification.schema.json")):
                        continue
                    target = destination / name
                    if not target.resolve().is_relative_to(destination):
                        raise ValueError("Snapshot archive path escapes its destination")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if member.issym():
                        link = Path(member.linkname)
                        if link.is_absolute() or not (target.parent / link).resolve().is_relative_to(destination):
                            raise ValueError("Source symlink escapes the fresh snapshot")
                        target.symlink_to(link)
                    elif member.isfile():
                        stream = archive.extractfile(member)
                        head = stream.read(256)
                        if head.startswith(POINTER):
                            fields = dict(line.split(" ", 1) for line in head.decode().splitlines()[1:])
                            source = root / name
                            if (not source.is_file() or source.stat().st_size != int(fields["size"])
                                    or hashlib.sha256(source.read_bytes()).hexdigest() != fields["oid"].removeprefix("sha256:")):
                                raise ValueError("Hydrate authorized source/metadata LFS path before export: " + name)
                            shutil.copyfile(source, target)
                            hydrated += 1
                        else:
                            with target.open("wb") as out:
                                out.write(head)
                                shutil.copyfileobj(stream, out)
                        target.chmod(0o755 if member.mode & 0o111 else 0o644)
                    else:
                        raise ValueError("Unsupported source archive member")
                    count += 1
            if process.wait():
                raise ValueError("Source archive failed")
        for name, entry in retained.items():
            source = entry.get("source") or stage_payload.contained(root, name)
            if not stage_payload.authentic(source, entry):
                raise ValueError("Reviewed fixture changed during export")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if retained:
            with (destination / ".gitattributes").open("a") as attributes:
                attributes.write("\n# Explicitly retained files are ordinary Git blobs, not LFS pointers.\n")
                for name in sorted(retained):
                    attributes.write(json.dumps("/" + name) + " -filter -text\n")
        public_count = 0
        for name in public_tasks:
            task, entries = tasks[name]
            for entry in entries:
                if entry["scope"] != "public":
                    continue
                source = stage_payload.contained(task, entry["destination"])
                if not stage_payload.authentic(source, entry):
                    raise ValueError("Selected public input must be authenticated before snapshot export")
                target = destination / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target, symlinks=True)
                else:
                    shutil.copyfile(source, target)
                public_count += 1
        subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet",
                        "--initial-branch=public-source"], cwd=destination, check=True)
        (destination / ".git/info").mkdir(exist_ok=True)
        (destination / ".git/info/exclude").write_text(
            "".join("/" + path + "\n" for path in sorted(excluded)))
        git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.process=",
               "-c", "filter.lfs.clean=cat", "-c", "filter.lfs.required=false"]
        subprocess.run([*git, "add", "--all"], cwd=destination, check=True)
        if retained:
            subprocess.run([*git, "add", "--force", "--", *sorted(retained)], cwd=destination, check=True)
        subprocess.run([*git, "-c", "commit.gpgsign=false", "commit", "--quiet",
                        "-m", "Initialize hydrated source snapshot", "-m", "Assisted-by: Replit"],
                       cwd=destination, env=identity, check=True)
        receipt = {"schema": "optimbench-fresh-source-snapshot-v1", "tracked_code_files": count,
                   "hydrated_source_metadata": hydrated, "included_public_inputs": public_count,
                   "public_input_tasks": list(public_tasks), "donor_git_history_copied": False,
                   "retained_public_fixture_inventories": retention_receipts,
                   "scorer_metadata_bundles": metadata_receipts,
                   "retained_git_filters": "explicit retained paths have LFS/text filters disabled",
                   "operator_host_registry_copied": False, "publication_clearance": "not-evaluated"}
        destination.rename(output)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--public-inputs", action="append", default=[], metavar="TASK",
                        help="Include only authenticated public data for this task; not redistribution clearance")
    parser.add_argument("--retain-reviewed-public-fixtures", action="append", default=[], metavar="INVENTORY",
                        help="Retain only explicitly reviewed exact public fixtures/notices from this tracked inventory; not publication approval")
    parser.add_argument("--scorer-metadata", action="append", default=[], metavar="TASK",
                        help="Bundle only the approved original authored scorer metadata outside frozen Task paths")
    parser.add_argument("--scorer-metadata-from", type=Path, metavar="ORIGINALS",
                        help="Existing authenticated originals in stage_payload --from layout; never fetched or regenerated")
    args = parser.parse_args(argv)
    print(json.dumps(export(ROOT, args.out, args.public_inputs, args.retain_reviewed_public_fixtures,
                            args.scorer_metadata, args.scorer_metadata_from), indent=2))


if __name__ == "__main__":
    main()
