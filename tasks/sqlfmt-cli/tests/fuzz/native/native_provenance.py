"""Reference/CPU attribution provenance. Never use the edit-policy partial lock."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

TASK = Path(__file__).resolve().parents[3]
WORKLOAD = TASK / "environment/workload"
LOCK = TASK / "tests/fuzz/native/native-source.lock.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory(source):
    result = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError("Original source symlink")
        if path.is_file():
            result[relative.as_posix()] = digest(path)
    return result


def authenticate_source(source):
    expected = json.loads(LOCK.read_text())["files_sha256"]
    if source_inventory(source) != expected:
        raise ValueError("Complete original source inventory mismatch")
    return expected


def verify_build(build):
    expected = authenticate_source(build / "project")
    receipt = json.loads((build / "native-build-receipt.json").read_text())
    if (receipt["source_files_sha256"] != expected
            or receipt["driver_sha256"] != digest(WORKLOAD / "native/main.go")
            or receipt["recipe_sha256"] != digest(WORKLOAD / "build.sh")
            or receipt["toolchain"] != "go version go1.26.9 linux/amd64"
            or receipt["flags"] != ["-trimpath"] or receipt["cgo_enabled"] is not False):
        raise ValueError("Original build receipt does not bind the expected source and recipe")
    for name, value in receipt["binaries"].items():
        if name not in ("sqlfmt", "sqlfmt-native") or digest(build / name) != value:
            raise ValueError("Rebuilt executable checksum mismatch")
    if set(receipt["binaries"]) != {"sqlfmt", "sqlfmt-native"}:
        raise ValueError("Incomplete rebuilt executable inventory")
    return receipt


def fresh_original_build(archive, destination, image):
    """A caller cannot supply an arbitrary executable as a reference authority."""
    expected = json.loads(LOCK.read_text())
    if digest(archive) != expected["archive_sha256"]:
        raise ValueError("Pinned original archive mismatch")
    if destination.exists():
        raise ValueError("Reference authoring requires a fresh build destination")
    source = destination.with_name(destination.name + "-original")
    source.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as tar:
        for member in tar:
            relative = Path(*Path(member.name).parts[1:])
            if not member.isfile():
                continue
            if relative.as_posix() not in expected["files_sha256"]:
                raise ValueError("Unexpected original archive member")
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    authenticate_source(source)
    destination.mkdir()
    log = destination / "reference-build.log"
    with log.open("wb") as stream:
        result = subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--cpuset-cpus", "0-7",
            "-v", f"{source}:/original:ro", "-v", f"{destination}:/build",
            "-v", f"{WORKLOAD}:/app/workload:ro", "-e", "OPTIMBENCH_SOURCE=/original",
            image, "sh", "/app/workload/build.sh", "/build",
        ], stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise ValueError("Protected original reference build failed")
    return verify_build(destination)
