"""Protected filesystem contract for one real, in-place SQLFluff fix operation."""
import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import stat

from runner import Step, canonical_json, contained

BEFORE = "autofix-before.json"


def snapshot(root):
    """Retain full evidence, including volatile metadata, outside the SQL tree."""
    records = {}
    for path in sorted([root, *root.rglob("*")]):
        info = path.lstat()
        row = {
            "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid,
            "nlink": info.st_nlink, "inode": info.st_ino,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "atime_ns": info.st_atime_ns,
        }
        if path.is_symlink():
            row.update(kind="symlink", target=os.readlink(path))
        elif path.is_dir():
            row["kind"] = "directory"
        elif path.is_file():
            raw = path.read_bytes()
            row.update(kind="file", sha256=hashlib.sha256(raw).hexdigest(),
                       content_base64=base64.b64encode(raw).decode("ascii"))
        else:
            row["kind"] = "special"
        records[path.relative_to(root).as_posix()] = row
    return records


def capture(scratch):
    before = {"files": snapshot(scratch / "project"),
              "actor": {"uid": os.geteuid(), "gid": os.getegid()}}
    (scratch / BEFORE).write_text(json.dumps(before, sort_keys=True))


def prepare(case, ctx):
    dialect = case.data.get("dialect", "postgres")
    if dialect not in ("postgres", "bigquery", "snowflake"):
        raise ValueError("Auto-fix requires a declared supported source dialect")
    project = ctx.copy_project(case.data["project"], case.data["inventory_sha256"])
    targets = case.data["targets"]
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("Auto-fix requires distinct original SQL targets")
    for name in targets:
        relative = Path(name)
        if (relative.is_absolute() or relative.as_posix() != name
                or ".." in relative.parts or name.startswith("-")):
            raise ValueError("Auto-fix requires canonical relative SQL paths")
        if any(project.joinpath(*relative.parts[:index]).is_symlink()
               for index in range(1, len(relative.parts) + 1)):
            raise ValueError("Auto-fix targets cannot traverse symbolic links")
        path = contained(project, name)
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".sql":
            raise ValueError("Auto-fix targets must be original regular SQL files")
    # Match the shared Docker scratch preparation before taking the local
    # snapshot. The authenticated container hook takes the authoritative Docker
    # snapshot after its own setup, not before the runner changes permissions.
    for path in (project, *project.rglob("*")):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | (0o777 if path.is_dir() else 0o666))
    capture(ctx.scratch)
    return [Step(
        ["/usr/local/bin/python", "-m", "sqlfluff", "fix", *targets,
         f"--dialect={dialect}", "--exclude-rules=AL07,LT05,LT12",
         "--processes", "1", "--show-lint-violations",
         "--disable-progress-bar", "--nocolor", "--ignore-local-config",
         "--config", str(ctx.workload / "driver/autofix.cfg")],
        "project",
        {"PYTHONPATH": str(ctx.build / "runtime"),
         "XDG_CACHE_HOME": str(ctx.scratch / "cache"),
         "XDG_CONFIG_HOME": str(ctx.scratch / "config")},
        case.data["timeout"],
    )]


def observations(before, after, targets, actor):
    """Full final bytes plus invariant checks, not an arbitrary success flag.

    Raw device-local identities and timestamps remain in the evidence snapshots.
    Changed files must have the operation user's ownership and retain their mode.
    Unchanged entries retain all metadata except observer-affected access time.
    Directory timestamps may change when a descendant is safely replaced.
    Physical directory link counts vary by filesystem. Check them within each
    operation, but omit them from the portable final record.
    """
    changed = {name for name in set(before) | set(after)
               if before.get(name, {}).get("sha256") != after.get(name, {}).get("sha256")}
    parents = {parent.as_posix() for name in changed for parent in Path(name).parents}
    violations, files = [], {}
    if set(before) != set(after):
        violations.append("project-inventory-changed")
    for name, row in after.items():
        old = before.get(name, {})
        files[name] = {key: value for key, value in row.items()
                       if key in ("kind", "mode", "nlink", "content_base64", "target")}
        if row["kind"] == "directory":
            files[name].pop("nlink")
        if name in changed and name in targets:
            if (row["kind"] != "file" or row["mode"] != old.get("mode")
                    or row["uid"] != actor["uid"] or row["gid"] != actor["gid"]):
                violations.append(name + ": replacement-permissions")
            continue
        if name in changed:
            violations.append(name + ": non-target-content-changed")
        volatile = {"atime_ns"}
        if row["kind"] == "directory" and name in parents:
            volatile.update(("mtime_ns", "ctime_ns", "size"))
        if {k: v for k, v in row.items() if k not in volatile} != {
                k: v for k, v in old.items() if k not in volatile}:
            violations.append(name + ": protected-metadata-changed")
    return {"files": files, "filesystem_violations": sorted(violations)}


def collect(case, ctx, results):
    if len(results) != 1:
        raise ValueError("The complete auto-fix operation must run exactly once")
    before = json.loads((ctx.scratch / BEFORE).read_text())
    after = snapshot(ctx.scratch / "project")
    (ctx.scratch / "autofix-after.json").write_text(json.dumps(after, sort_keys=True))
    # Preserve every original stream byte. Legacy lint retains its own unchanged
    # stderr contract, but this newly frozen serial operation has a raw contract.
    result = dataclasses.replace(
        results[0],
        stdout=(ctx.scratch / "step-0.stdout").read_bytes(),
        stderr=(ctx.scratch / "step-0.stderr").read_bytes(),
    )
    record = {"format": "sqlfluff-project-autofix-v1", **result.streams(),
              **observations(before["files"], after, set(case.data["targets"]), before["actor"])}
    return {case.id: canonical_json(record)}
