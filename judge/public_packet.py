"""Optional declared runtime data and original protected public derivations."""
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import tomllib

from tools import stage_payload

NAME = "public-input-packet.json"
SCHEMA = "optimbench-public-input-packet-v1"


def relative(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..", ".git") for part in value.split("/"))):
        raise ValueError("Public packet paths must be normalized relative paths")
    return value


def visible(packet, path):
    return any(path == root or path.startswith(root + "/") for root in packet["mounts"])


def load(task):
    path = Path(task) / "environment" / NAME
    if not path.exists():
        return None
    raw = path.read_bytes()
    packet = json.loads(raw)
    if (not isinstance(packet, dict) or packet.get("schema") != SCHEMA
            or set(packet) - {"schema", "mounts", "derived_inputs", "prepare"}):
        raise ValueError("Invalid public input packet declaration")
    mounts = packet.get("mounts")
    if (not isinstance(mounts, list) or not mounts
            or any(not isinstance(item, str) for item in mounts)
            or len(mounts) != len(set(mounts))):
        raise ValueError("Public packet requires distinct visible workload roots")
    for root in mounts:
        relative(root)
    derived = packet.get("derived_inputs", [])
    if not isinstance(derived, list):
        raise ValueError("Public packet derived inputs must be a list")
    seen = set()
    for entry in derived:
        if not isinstance(entry, dict) or set(entry) != {"destination", "bytes", "sha256"}:
            raise ValueError("Derived public inputs require exact path, byte count and SHA-256")
        destination = relative(entry["destination"])
        prefix = "environment/workload/"
        if (not destination.startswith(prefix) or destination in seen
                or not visible(packet, destination[len(prefix):])
                or len(PurePosixPath(destination).parts) < 4
                or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str)
                or re.fullmatch("[0-9a-f]{64}", entry["sha256"]) is None):
            raise ValueError("Invalid or non-visible derived public input commitment")
        seen.add(destination)
    prepare = packet.get("prepare")
    if prepare is not None:
        if not isinstance(prepare, dict) or set(prepare) != {"image", "argv", "write_dirs"}:
            raise ValueError("Protected preparation requires a pinned image, argv and exact output directories")
        if (not isinstance(prepare["image"], str)
                or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", prepare["image"]) is None
                or not isinstance(prepare["argv"], list) or not prepare["argv"]
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in prepare["argv"])):
            raise ValueError("Invalid pinned public preparation image or argv")
        parents = {str(PurePosixPath(entry["destination"]).parent).removeprefix("environment/")
                   for entry in derived}
        directories = prepare["write_dirs"]
        if (not isinstance(directories, list) or not directories
                or any(not isinstance(item, str) for item in directories)
                or len(directories) != len(set(directories))):
            raise ValueError("Invalid public preparation write directories")
        if {relative(item) for item in directories} != parents:
            raise ValueError("Preparation may write only the declared derived output directories")
    return {**packet, "derived_inputs": derived, "sha256": hashlib.sha256(raw).hexdigest()}


def mount(source, target, readonly=True):
    result = io.StringIO()
    csv.writer(result, lineterminator="").writerow(
        ["type=bind", "source=" + str(source), "target=" + target] + (["readonly"] if readonly else []))
    return result.getvalue()


def ensure_derived(task, packet, *, fetch, source=None):
    entries = packet["derived_inputs"]
    missing = []
    for entry in entries:
        target = stage_payload.contained(task, entry["destination"])
        cached = stage_payload.source_candidate(source, entry) if source is not None and fetch else None
        if cached is not None and not stage_payload.authentic(cached, entry):
            raise ValueError("Derived runtime cache differs from its original commitment")
        if target.exists():
            if not stage_payload.authentic(target, entry):
                raise ValueError("Derived public input differs from its original commitment")
            continue
        if cached is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                copied = Path(temporary) / "body"
                shutil.copyfile(cached, copied)
                if not stage_payload.authentic(copied, entry):
                    raise ValueError("Derived public cache changed while copying")
                copied.rename(target)
        else:
            missing.append(entry)
    if missing:
        if not fetch or not packet.get("prepare"):
            raise ValueError("Declared derived public inputs are missing; run original protected preparation")
        environment = Path(task) / "environment"
        settings = tomllib.loads((Path(task) / "task.toml").read_text())["environment"]
        command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                   "--cpus", str(settings["cpus"]), "--memory", str(settings["memory_mb"]) + "m",
                   "--tmpfs", "/tmp:rw,nosuid,nodev", "--mount", mount(environment, "/payload")]
        for directory in packet["prepare"]["write_dirs"]:
            target = stage_payload.contained(environment, directory)
            target.mkdir(parents=True, exist_ok=True)
            command.extend(["--mount", mount(target, "/payload/" + directory, readonly=False)])
        command.extend([packet["prepare"]["image"], *packet["prepare"]["argv"]])
        subprocess.run(command, capture_output=True, check=True)
    for entry in entries:
        if not stage_payload.authentic(stage_payload.contained(task, entry["destination"]), entry):
            raise ValueError("Protected public preparation did not reproduce the original bytes")
    current = load(task)
    if current is None or current["sha256"] != packet["sha256"]:
        raise ValueError("Public input packet declaration changed during preparation")
    return len(missing)
