"""Task-local pinned source/anchor helpers for the two workspace controls."""
import hashlib
import json
import os
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def contained(root, name):
    path = Path(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("mutation path must be source-relative")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise ValueError("source path escapes or is a symlink")
    return target


def source_identity(root):
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            if Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("source contains an absolute or escaping symlink")
            rows.append((str(relative), "symlink", os.readlink(path)))
        elif path.is_file():
            rows.append((str(relative), "file", path.stat().st_mode & 0o777, sha(path.read_bytes())))
    return sha(json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode())


def apply_recipe(root, recipe):
    if recipe.get("language") != "c" or not recipe.get("edits"):
        raise ValueError("missing C source edits")
    original, changed = {}, {}
    for edit in recipe["edits"]:
        name = edit["path"]
        target = contained(root, name)
        if name not in original:
            original[name] = target.read_bytes()
            changed[name] = original[name].decode("utf-8")
        if sha(original[name]) != edit["original_sha256"]:
            raise ValueError("stale source anchor: " + name)
        before, after = edit["before"], edit["after"]
        if not isinstance(before, str) or not before or before == after:
            raise ValueError("empty/no-op source control")
        if changed[name].count(before) != 1:
            raise ValueError("anchor must match exactly once: " + name)
        changed[name] = changed[name].replace(before, after, 1)
    for name, text in changed.items():
        contained(root, name).write_text(text, encoding="utf-8")
    return [{"path": name, "before_sha256": sha(original[name]),
             "after_sha256": sha(text.encode())} for name, text in sorted(changed.items())]
