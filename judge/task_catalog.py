"""Canonical packaged-task coverage for repository-level checks."""
import json
from pathlib import Path


def task_paths(root):
    """Require a nonempty, exact catalog/directory match without fixed counts."""
    root = Path(root)
    names = json.loads((root / "datasets/all.json").read_text())["tasks"]
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name or Path(name).name != name
                   or name in (".", "..") for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Canonical task catalog must contain unique, nonempty task names")
    directories = {path.name: path for path in (root / "tasks").iterdir() if path.is_dir()}
    missing = set(names) - directories.keys()
    unknown = directories.keys() - set(names)
    if missing or unknown:
        raise ValueError(f"Task catalog/directory mismatch: missing={sorted(missing)}, "
                         f"unknown={sorted(unknown)}")
    for name in names:
        if not (directories[name] / "task.json").is_file():
            raise ValueError("Catalog task is missing task.json: " + name)
    return [directories[name] for name in sorted(names)]
