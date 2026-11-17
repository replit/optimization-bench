"""Authenticate/materialize ONLY public verifier inputs before generation.

Use the existing payload acquisition/authentication code, not its catalog-writing
CLI. No private body is acquired, no global LFS pull is performed, and an image's
private/public cache is not a substitute for the verifier checkout's actual bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import heldout, public_packet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from tools import stage_payload

POINTER = b"version https://git-lfs.github.com/spec/v1\n"


class PublicInputsError(ValueError):
    def __init__(self, reason, commands=()):
        self.commands = list(commands)
        super().__init__(reason + "".join("\n  Run: " + command for command in commands))


def _manifest(task, root):
    root, task = Path(root).resolve(), heldout.canonical_task(task)
    if not task.is_relative_to(root / "tasks") or task == root / "tasks":
        raise PublicInputsError("Public input task must belong to this verifier checkout")
    path = task / "payload.json"
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if any(e.get("scope") not in ("public", "heldout") for e in manifest["files"]):
        raise PublicInputsError("Payload entries must have explicit public/heldout scopes")
    public = [e for e in manifest["files"] if e["scope"] == "public"]
    if not public:
        raise PublicInputsError("No explicit public input inventory; do not generate from other splits")
    seen = set()
    for entry in public:
        destination = entry["destination"]
        if not destination.startswith("environment/") or destination in seen:
            raise PublicInputsError("Invalid/duplicate public environment destination")
        seen.add(destination)
        stage_payload.contained(task, destination)
    return manifest, public, hashlib.sha256(raw).hexdigest()


def needs_ffmpeg(task, *, root=ROOT):
    _, public, _ = _manifest(task, root)
    def walk(value):
        if isinstance(value, dict):
            return value.get("kind") == "png-rgb24" or any(walk(v) for v in value.values())
        return isinstance(value, list) and any(walk(v) for v in value)
    return any(walk(e) for e in public)


def _lfs_sources(task, absent, root):
    from payload_check import repo_lfs_spec
    paths = []
    for entry in absent:
        if entry.get("kind") == "repo-lfs":
            try:
                if stage_payload.authentic(stage_payload.contained(task, entry["destination"]), entry):
                    continue
            except ValueError:
                pass  # A pointer body needs scoped recovery, never acceptance.
            path = stage_payload.contained(task, repo_lfs_spec(entry))
            try:
                if stage_payload.authentic(path, entry):
                    continue
            except ValueError:
                pass
        else:
            path = stage_payload.contained(task, entry["destination"])
            if not path.is_file():
                if entry.get("fetch") or entry.get("url") or entry.get("kind") == "git-history":
                    continue
                relative = path.relative_to(Path(root).resolve()).as_posix()
                try:
                    checked = subprocess.run(
                        ["git", "check-attr", "--cached", "filter", "--", relative],
                        cwd=root, capture_output=True, text=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if checked.returncode != 0 or not checked.stdout.strip().endswith(": lfs"):
                    continue
            else:
                with path.open("rb") as stream:
                    if not stream.read(128).startswith(POINTER):
                        continue
        paths.append(path.relative_to(Path(root).resolve()).as_posix())
    return sorted(set(paths))


def _runtime_workload(task, entries, source, packet=None):
    restored = 0
    for entry in entries:
        prefix = "environment/workload/"
        if not entry["destination"].startswith(prefix):
            continue
        destination = stage_payload.contained(source, entry["destination"][len(prefix):])
        if packet is not None and not public_packet.visible(packet, entry["destination"][len(prefix):]):
            if destination.exists():
                raise PublicInputsError("Acquisition-only public body must not appear in the runtime workload")
            continue
        if destination.exists():
            if not stage_payload.authentic(destination, entry):
                raise PublicInputsError("Runtime public cache differs from the authenticated input inventory")
            continue
        original = stage_payload.contained(task, entry["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            prepared = Path(temporary) / "body"
            if original.is_dir():
                shutil.copytree(original, prepared, symlinks=True)
            else:
                shutil.copyfile(original, prepared)
            if not stage_payload.authentic(prepared, entry):
                raise PublicInputsError("Public input changed while preparing the runtime cache")
            prepared.rename(destination)
        restored += 1
    return restored


def ensure_public_inputs(task, *, fetch=True, source=None, root=ROOT):
    """Call before differential.harness()/generate, never during timing.

    `fetch=False` is a read-only readiness check. `fetch=True` stages pinned
    public fetch recipes only and then verifies real body/history commitments.
    The consolidator's batch-rollback stage_payload implementation is reused
    automatically when integrated; this helper never refreshes catalogs.
    """
    task, root = heldout.canonical_task(task), Path(root).resolve()
    manifest, public, manifest_sha = _manifest(task, root)
    packet = public_packet.load(task)
    command = shlex.quote(sys.executable) + " -m judge.public_input_preflight --task " + shlex.quote(task.name) + " --fetch"
    restored = 0
    fallback = 0
    if source is not None and fetch:
        try:
            # A cache is useful only after its actual body bytes have been
            # authenticated and copied into the selected public checkout paths.
            # stage() validates all retention/path metadata and rolls back the
            # complete batch on failure; no private scope is selected.
            for entry in public:
                try:
                    present = stage_payload.authentic_destination((task, entry))
                except ValueError:
                    present = False
                if not present and stage_payload.source_candidate(source, entry) is not None:
                    restored += 1
            staged = stage_payload.stage(task, source=source, fetch=True, scope="public")
            fallback = staged - restored
        except Exception as exc:
            raise PublicInputsError("Public cache restoration failed (" + type(exc).__name__ +
                                    "); no differential cases may be generated") from exc
    # repo-lfs authentication deliberately raises on pointers. Produce scoped
    # LFS recovery instructions before invoking the stricter missing() scan.
    lfs = _lfs_sources(task, public, root)
    if lfs:
        include = shlex.quote(",".join(lfs))
        checkout = " ".join(shlex.quote(path) for path in lfs)
        raise PublicInputsError("Public LFS bodies unavailable; private/global LFS acquisition is forbidden", [
            "git lfs install --local --skip-smudge",
            "git lfs pull --include=" + include + " --exclude=" + shlex.quote(""),
            "git lfs checkout " + checkout, command])
    # Validates all declared paths and private-reference metadata, but scope
    # filtering ensures no private contents are fetched or materialized.
    try:
        absent = stage_payload.missing(task, scope="public")
    except (OSError, ValueError, KeyError) as exc:
        raise PublicInputsError("Public payload metadata invalid: " + type(exc).__name__) from exc
    if absent and not fetch:
        raise PublicInputsError("Public input bodies missing or corrupt in verifier checkout: " +
                                ", ".join(e["destination"] for e in absent), [command])
    if absent:
        if needs_ffmpeg(task, root=root) and shutil.which("ffmpeg") is None:
            from tools.scoring_host import apt
            raise PublicInputsError("Public RGB24 conversion requires ffmpeg before acquisition",
                                    [apt("ffmpeg"), command])
        try:
            stage_payload.stage(task, fetch=True, scope="public")
        except Exception as exc:
            # Do not echo upstream URLs/transport configuration (which could
            # contain credentials). Exact public paths remain in the manifest.
            raise PublicInputsError("Public input acquisition failed (" + type(exc).__name__ +
                                    "); no differential cases may be generated", [command]) from exc
    if hashlib.sha256((task / "payload.json").read_bytes()).hexdigest() != manifest_sha:
        raise PublicInputsError("Payload manifest changed while staging")
    members = []
    # Independent per-entry authentication runs concurrently; results are
    # consumed in manifest order so the first reported failure is unchanged.
    results = heldout.concurrent_outcomes(
        stage_payload.authentic_destination, [(task, entry) for entry in public])
    for entry, result in zip(public, results):
        target = stage_payload.contained(task, entry["destination"])
        if not heldout.outcome(result):
            raise PublicInputsError("Public input still missing/corrupt after staging: " + entry["destination"], [command])
        row = {"destination": entry["destination"], "kind": entry.get("kind", "file"),
               "verified": True}
        if target.is_file():
            with target.open("rb") as stream:
                head = stream.read(128)
                if head.startswith(POINTER):
                    raise PublicInputsError("Public input is still a Git LFS pointer: " + entry["destination"])
                stream.seek(0)
                row.update(bytes=target.stat().st_size, sha256=hashlib.file_digest(stream, "sha256").hexdigest())
        else:
            row["binding"] = "authenticated git-history inventory in payload manifest"
        members.append(row)
    derived = packet["derived_inputs"] if packet is not None else []
    try:
        prepared = public_packet.ensure_derived(task, packet, fetch=fetch, source=source) if packet else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PublicInputsError("Original protected public preparation failed (" +
                                type(exc).__name__ + ")", [command]) from exc
    runtime_restored = (_runtime_workload(task, public + derived, source, packet)
                        if source is not None and fetch else 0)
    return {"schema": "optimbench-public-input-preflight-v1", "task": task.name,
            "cache_restored_entries": restored,
            "source_fetch_fallback_entries": fallback,
            "runtime_cache_materialized_entries": runtime_restored,
             "derived_public_entries": len(derived), "prepared_derived_entries": prepared,
            "payload_manifest_sha256": manifest_sha, "scope": "public",
            "staged_entries": len(absent), "verified_entries": len(members), "members": members,
            "private_acquisition": False, "catalog_mutation": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        value = ensure_public_inputs(ROOT / "tasks" / args.task, fetch=args.fetch)
        if args.out:
            args.out.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(json.dumps(value, indent=2, sort_keys=True))
    except (PublicInputsError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
