#!/usr/bin/env python3
"""Download and verify a Task's public runtime inputs for a sandbox that cannot mount them.

Five Tasks (jq, serde_json, sqlfmt, SQLGlot, Terser) publish input-free images: their
public inputs are normally attached read-only at /app/workload/inputs/files when the
container starts. Hosted sandboxes that start from the image alone cannot do that, so
run this from a temporary checkout inside the sandbox before the agent starts, then delete
the checkout (the agent must not see the repository or tests/):

    GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --depth 1 --branch optimbench-v1.0.0 \
      https://github.com/replit/optimization-bench.git /tmp/optimbench
    python3 /tmp/optimbench/tools/fetch_public_inputs.py --task TASK
    rm -rf /tmp/optimbench   # the agent must not see the repository or tests/

It downloads every public input from its pinned original publisher (or Git LFS for
bodies stored in this repository), rebuilds declared derived inputs locally, checks
every file's size and SHA-256, and places the files in --dest. Held-out inputs are
never touched, and downloading an input means accepting its publisher's terms (see
the Task's notices and docs/public-data-policy.md).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from judge import public_packet  # noqa: E402
from tools import stage_payload  # noqa: E402

MOUNT = "inputs/files"
PREFIX = "environment/workload/" + MOUNT + "/"
POINTER = b"version https://git-lfs.github.com/spec/v1"


class FetchError(RuntimeError):
    pass


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_mounted(task, packet):
    if packet is not None:
        if packet["mounts"] != [MOUNT]:
            raise FetchError("Unsupported packet mounts: " + ", ".join(packet["mounts"]))
        return True
    dockerfile = task / "environment/Dockerfile"
    return dockerfile.is_file() and "/app/workload/inputs" in dockerfile.read_text() and \
        "--mount" in dockerfile.read_text()


def lfs_pointers(task, entries):
    pointers = []
    for entry in entries:
        if entry.get("kind") != "repo-lfs":
            continue
        from payload_check import repo_lfs_spec
        path = stage_payload.contained(task, repo_lfs_spec(entry))
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(len(POINTER)) == POINTER:
                    pointers.append(path.relative_to(ROOT).as_posix())
    return pointers


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def recover_lfs(pointers):
    """Replace the given Git LFS pointer files with their bodies, verified against each pointer's oid and size.

    Uses `git lfs` when installed; otherwise asks the Git LFS batch API of this checkout's
    GitHub origin for each object (anonymous for a public repository; a GITHUB_TOKEN is sent
    only if set, for private forks)."""
    if shutil.which("git-lfs"):
        subprocess.run(["git", "-C", str(ROOT), "lfs", "pull", "--include=" + ",".join(pointers), "--exclude="],
                       check=True)
        return
    import base64
    import os
    import re
    import urllib.request
    try:
        remote = git("remote", "get-url", "origin")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FetchError("Git LFS bodies are needed; install git-lfs or run from a git clone of the repository") from exc
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", remote)
    if not match:
        raise FetchError("Git LFS bodies are needed and origin is not GitHub; install git-lfs")
    owner, repo = match.groups()
    headers = {"Accept": "application/vnd.git-lfs+json", "Content-Type": "application/vnd.git-lfs+json",
               "User-Agent": "optimbench-fetch-public-inputs"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Basic " + base64.b64encode(
            ("x-access-token:" + os.environ["GITHUB_TOKEN"]).encode()).decode()
    for relative in pointers:
        path = ROOT / relative
        fields = dict(line.split(" ", 1) for line in path.read_text().splitlines() if " " in line)
        oid, size = fields["oid"].removeprefix("sha256:"), int(fields["size"])
        batch = urllib.request.Request(
            f"https://github.com/{owner}/{repo}.git/info/lfs/objects/batch", headers=headers, method="POST",
            data=json.dumps({"operation": "download", "transfers": ["basic"],
                             "objects": [{"oid": oid, "size": size}]}).encode())
        with urllib.request.urlopen(batch, timeout=60) as response:
            obj = json.load(response)["objects"][0]
        if "actions" not in obj or "download" not in obj["actions"]:
            raise FetchError("Git LFS server has no download for " + relative + ": " + json.dumps(obj.get("error")))
        action = obj["actions"]["download"]
        request = urllib.request.Request(action["href"], headers=dict(action.get("header", {}),
                                                                      **{"User-Agent": headers["User-Agent"]}))
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            staged = Path(temporary.name)
            with urllib.request.urlopen(request, timeout=300) as response:
                shutil.copyfileobj(response, temporary)
        if staged.stat().st_size != size or sha256(staged) != oid:
            staged.unlink()
            raise FetchError("Git LFS body failed its oid/size check: " + relative)
        staged.replace(path)


def fetch(task_name, dest, image_manifest):
    task = stage_payload.contained(ROOT / "tasks", task_name)
    if not (task / "payload.json").is_file():
        raise FetchError("Unknown Task: " + task_name)
    packet = public_packet.load(task)
    if not runtime_mounted(task, packet):
        raise FetchError(task_name + "'s image already contains its public inputs; nothing to fetch")
    # The image's own manifest must be the one these inputs are pinned for: check before downloading.
    if image_manifest is not None and image_manifest.is_file():
        expected = task / "environment/workload/inputs/manifest.json"
        if sha256(image_manifest) != sha256(expected):
            raise FetchError("This checkout's input manifest does not match the image's "
                             + str(image_manifest) + "; check out the release that matches the image")
    public = [e for e in json.loads((task / "payload.json").read_text())["files"] if e["scope"] == "public"]

    pointers = lfs_pointers(task, public)
    if pointers:
        recover_lfs(pointers)

    # 1. Pinned public bodies: the same acquisition and SHA-256 checks as the image build.
    stage_payload.stage(task, fetch=True, scope="public")
    # 2. Declared derived inputs: the Task's own offline derivation, run in-process.
    derived = packet.get("derived_inputs", []) if packet else []
    if derived:
        environment = task / "environment"
        todo = [e for e in derived if not stage_payload.contained(task, e["destination"]).is_file()]
        if todo:
            argv = packet["prepare"]["argv"]
            if argv[:3] != ["python3", "-I", "/payload/workload/fetch_inputs.py"] or argv[3:] != ["/payload"]:
                raise FetchError("Unsupported derivation command in public-input-packet.json")
            subprocess.run([sys.executable, "-I", str(environment / "workload/fetch_inputs.py"), str(environment)],
                           check=True)
    # 3. Copy exactly the runtime-visible inputs, verifying each one.
    rows = [e for e in public if e["destination"].startswith(PREFIX)] + list(derived)
    if not rows:
        raise FetchError("No runtime public inputs declared for " + task_name)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for entry in rows:
        source = stage_payload.contained(task, entry["destination"])
        if not stage_payload.authentic(source, entry):
            raise FetchError("Input failed its pinned size/SHA-256 check: " + entry["destination"])
        relative = entry["destination"][len(PREFIX):]
        target = stage_payload.contained(dest, relative)
        if target.is_file() and target.stat().st_size == entry["bytes"] and sha256(target) == entry["sha256"]:
            total += entry["bytes"]
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            staged = Path(temporary.name)
        shutil.copyfile(source, staged)
        if staged.stat().st_size != entry["bytes"] or sha256(staged) != entry["sha256"]:
            staged.unlink()
            raise FetchError("Input changed while copying: " + entry["destination"])
        staged.chmod(0o444)
        staged.replace(target)
        total += entry["bytes"]
    return {"task": task_name, "dest": str(dest), "files": len(rows), "bytes": total,
            "image_manifest_checked": bool(image_manifest is not None and image_manifest.is_file())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--dest", type=Path, default=Path("/app/workload/inputs/files"))
    parser.add_argument("--image-manifest", type=Path, default=Path("/app/workload/inputs/manifest.json"),
                        help="manifest inside the image to match against (skipped if absent)")
    args = parser.parse_args()
    try:
        print(json.dumps(fetch(args.task, args.dest.resolve(), args.image_manifest), indent=2))
    except (FetchError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, "fetch_public_inputs: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
