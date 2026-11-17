"""Portable protected recipe/source intent, separate from runtime image attestation."""
import hashlib
import json
from pathlib import Path
import subprocess

from . import heldout, public_packet

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "protected-image-recipe-source-v1"
LABEL = "org.optimbench.recipe-sha256"
AGENT_ONLY_CONTEXT_PATHS = ("agent.Dockerfile", "docker-compose.yaml", "agent-tools/**")


def agent_only_context_path(relative):
    """Explicit agent-only context allowlist; all other files remain protected."""
    raw = str(relative)
    if (raw.startswith("/") or "\\" in raw or ".." in raw or
            any(not segment for segment in raw.split("/"))):
        return False
    name = Path(raw).as_posix()
    return any(name.startswith(pattern[:-2]) if pattern.endswith("/**")
               else name == pattern for pattern in AGENT_ONLY_CONTEXT_PATHS)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def descriptor(task):
    task = heldout.canonical_task(task)
    environment = task / "environment"
    payload = json.loads((task / "payload.json").read_text())
    public = [entry for entry in payload["files"] if entry["scope"] == "public"]
    destinations = [entry["destination"] for entry in public]
    packet = public_packet.load(task)
    if packet is not None:
        destinations.extend(entry["destination"] for entry in packet["derived_inputs"])
    tracked = subprocess.run(["git", "ls-files", "-z", "--", str(environment)],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    files = {}
    for name in tracked:
        if not name:
            continue
        path = ROOT / name
        if agent_only_context_path(path.relative_to(environment)):
            continue
        relative = path.relative_to(task).as_posix()
        if any(relative == destination or relative.startswith(destination.rstrip("/") + "/")
               for destination in destinations):
            continue  # Body commitment is authoritative and independently authenticated.
        if path.is_symlink():
            value = {"symlink": str(path.readlink())}
        else:
            value = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "executable": bool(path.stat().st_mode & 0o111)}
        files[path.relative_to(environment).as_posix()] = value
    if "Dockerfile" not in files:
        raise ValueError("Portable recipe identity requires tracked protected Dockerfile")
    if packet is not None and public_packet.NAME not in files:
        raise ValueError("Public input packet declaration must be tracked protected recipe context")
    release = json.loads((task / "release.json").read_text())
    value = {"format": FORMAT, "task": task.name, "context": files,
             "public_payload_commitment_sha256": digest(public),
             "baseline_source_sha256": release.get("baseline_source_sha256")}
    return {**value, "recipe_sha256": digest(value)}


def observation(task, runtime_image_id):
    expected = descriptor(task)
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", '{{json .Config.Labels}}', runtime_image_id],
        capture_output=True, text=True, check=True)
    labels = json.loads(result.stdout) or {}
    observed = labels.get(LABEL)
    return {"format": FORMAT, "recipe_sha256": expected["recipe_sha256"],
            "baseline_source_sha256": expected["baseline_source_sha256"],
            "runtime_image_id": runtime_image_id, "observed_recipe_sha256": observed,
            "recipe_label_matches": observed == expected["recipe_sha256"],
            "identity_basis": "protected recipe/source intent; runtime image ID retained",
            "binary_reproducibility_claimed": False}
