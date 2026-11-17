"""Protected task-owned, untimed container preparation metadata."""
import hashlib
import json
import base64
from pathlib import Path


def metadata(task):
    if task is None:
        return None
    task = Path(task)
    definition = task / "task.json"
    if not definition.is_file():
        return None
    value = json.loads(definition.read_text())
    if "container_prepare" not in value:
        return None
    argv = value["container_prepare"]
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) or "\0" in arg for arg in argv)):
        raise ValueError("container_prepare must be a nonempty argv")
    executable = Path(argv[0])
    prefix = Path("/app/workload")
    if (not executable.is_relative_to(prefix) or ".." in executable.parts
            or executable == prefix or executable.is_relative_to(prefix / "inputs")):
        raise ValueError("container_prepare executable must be protected task-owned workload code")
    settings = json.loads((task / "tests/judge.json").read_text())
    source = settings.get("source_path")
    if not isinstance(source, str) or not Path(source).is_absolute() or executable.is_relative_to(Path(source)):
        raise ValueError("container_prepare executable overlaps candidate source")
    root = (task / "environment/workload").resolve()
    raw = root / executable.relative_to(prefix)
    path = raw.resolve()
    if (not path.is_relative_to(root) or not path.is_file()
            or any(p.is_symlink() for p in [raw, *raw.parents] if p != root and p.is_relative_to(root))
            or not path.stat().st_mode & 0o111):
        raise ValueError("container_prepare requires a regular protected executable")
    return {"argv": argv, "executable_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "timeout": 600}


def validate_receipt(receipt, expected):
    """Provenance is validated independently, never compared as library output."""
    if (not isinstance(receipt, dict)
            or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0
            or receipt.get("harness_error") or receipt.get("timed") is not False
            or receipt.get("executable_sha256") != expected["executable_sha256"]
            or receipt.get("argv") != expected["argv"]
            or receipt.get("leftovers") != []
            or type(receipt.get("duration_ns")) is not int or receipt["duration_ns"] < 0):
        raise ValueError("Invalid container preparation receipt")
    for key in ("stdout_base64", "stderr_base64"):
        if not isinstance(receipt.get(key), str):
            raise ValueError("Invalid container preparation streams")
        try:
            base64.b64decode(receipt[key], validate=True)
        except ValueError as error:
            raise ValueError("Invalid container preparation streams") from error
