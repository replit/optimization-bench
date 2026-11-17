"""Harbor editable-root artifacts become a validated source envelope internally."""
import hashlib
import difflib
import fnmatch
import os
from pathlib import Path
import shutil

from . import source_policy, ruff_source_policy


def test_path(name, source=False):
    path = Path(name)
    parts = {part.lower() for part in path.parts}
    fixtures = {"fixtures", "fixture", "snapshots", "snapshot", "__snapshots__",
                "testdata", "expected", "expected-output", "expected-outputs",
                "expected_output"}
    tests = {"test", "tests", "__tests__"}
    if not source:
        return (bool(parts & (fixtures | tests)) or
                any(fnmatch.fnmatchcase(path.name, pattern) for pattern in
                    ("*.snap", "*.snapshot", "*.expected", "expected.*", "*.expected.*")))
    if parts & fixtures:
        return False
    return (path.suffix in {".py", ".rs", ".js", ".mjs", ".cjs", ".ts", ".tsx",
                            ".go", ".c", ".cc", ".cpp", ".h", ".java"} and
            (bool(parts & tests) or any(fnmatch.fnmatchcase(path.name, pattern)
             for pattern in ("test_*", "*_test.*", "*.test.*", "*.spec.*"))))


def additions_only(before, after):
    return all(tag in ("equal", "insert") for tag, *_ in
               difflib.SequenceMatcher(None, before.splitlines(keepends=True),
                                      after.splitlines(keepends=True), autojunk=False).get_opcodes())


def source_digest(admitted):
    if not admitted.get("test_changes"):
        return source_policy.digest(admitted["files"])
    return source_policy.digest({"files": admitted["files"], "test_changes": admitted["test_changes"]})


def protected_inventory(root, policy):
    result = {}
    root = Path(root)
    ignored = {".git", "node_modules", "__pycache__", ".pytest_cache"}
    declared_links = policy.get("protected_symlinks", {})
    seen_links = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ignored]
        for name in dirs + files:
            if name in ignored:
                continue
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                if (source_policy.allowed(relative, policy)
                        or (relative in declared_links and declared_links[relative] != target)
                        or Path(target).is_absolute()
                        or not path.resolve().is_relative_to(root.resolve())):
                    raise ValueError("Symlink in source submission: " + relative)
                # One rule for both imported mechanisms: record only safe,
                # protected aliases and compare their exact targets to pristine.
                # Optional declarations additionally authenticate known aliases.
                result[relative] = {"symlink": target}
                if relative in declared_links:
                    seen_links.add(relative)
                continue
            if path.is_file() and (not source_policy.allowed(relative, policy) or
                                  test_path(relative) or test_path(relative, source=True)):
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if seen_links != set(declared_links):
        raise ValueError("Declared protected symlink missing")
    return result


def protected_change_error(name, before, after, candidate, pristine):
    """Why envelope() refuses this protected-inventory difference; None for an admitted test addition."""
    if (name not in after or isinstance(after[name], dict) or
            not (test_path(name) or test_path(name, source=True))):
        return "Protected file changed: " + name
    if name in before:
        if not test_path(name, source=True):
            return "Protected fixture/snapshot changed: " + name
        try:
            original_text = (Path(pristine) / name).read_text()
            candidate_text = (Path(candidate) / name).read_text()
        except UnicodeError:
            return "Protected test is not source text: " + name
        if not additions_only(original_text, candidate_text):
            return "Existing test source modified: " + name
    return None


def envelope(candidate, pristine, policy):
    before, after = protected_inventory(pristine, policy), protected_inventory(candidate, policy)
    changes = {}
    for name in before.keys() | after.keys():
        if before.get(name) == after.get(name):
            continue
        problem = protected_change_error(name, before, after, candidate, pristine)
        if problem:
            raise ValueError(problem)
        changes[name] = after[name]
    original = source_policy.snapshot(pristine, policy, trusted_pristine=True)
    files = (original if Path(candidate).resolve() == Path(pristine).resolve() else
             source_policy.snapshot(candidate, policy, original=original))
    if "inline_protection" in policy:
        if set(files) != set(policy["allowed_files"]):
            raise ValueError("Ruff declared production-file set must remain exact")
        ruff_source_policy.validate_inline(files, policy, original=original)
    from .runtime_state import findings
    result = {"format": "source-envelope-v1", "files": files,
              "runtime_configuration_review": findings(original, files)}
    if changes:
        result["test_changes"] = changes
    return result


def artifacts_to_envelope(artifacts, pristine, destination, policy):
    """artifacts maps source-relative editable roots to Harbor-exported directories.

    Only declared roots are accepted. Full trees (including protected files within
    a root) replace the pristine root so deletions and extra files cannot disappear.
    """
    materialize(artifacts, pristine, destination, policy)
    return envelope(destination, pristine, policy)


def repair(artifacts, pristine, destination, policy):
    """Score-through: materialize a submission and undo its edits outside the editable scope.

    Protected files the candidate changed or deleted are restored from pristine, and
    protected files or symlinks it added are removed, so admission sees only the
    editable source. What envelope() still refuses afterwards (size, text, directive
    and exact-set checks) is the candidate's own and is not repaired.
    """
    materialize(artifacts, pristine, destination, policy, keep_symlinks=True)
    pristine, destination = Path(pristine), Path(destination)
    restored, removed = set(), set()

    def put_back(name):
        source, target = pristine / name, destination / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(source), target)
        elif source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            removed.add(name)
            return
        restored.add(name)

    ignored = {".git", "node_modules", "__pycache__", ".pytest_cache"}
    links = []
    for base, dirs, files in os.walk(destination, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ignored]
        links.extend(Path(base) / name for name in dirs + files if (Path(base) / name).is_symlink())
    for path in links:
        name = path.relative_to(destination).as_posix()
        original = pristine / name
        if not (original.is_symlink() and os.readlink(original) == os.readlink(path)):
            put_back(name)
    for name in policy.get("protected_symlinks", {}):
        if not (destination / name).is_symlink():
            put_back(name)
    before, after = protected_inventory(pristine, policy), protected_inventory(destination, policy)
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) != after.get(name) and protected_change_error(
                name, before, after, destination, pristine):
            put_back(name)
    return {"restored": sorted(restored), "removed": sorted(removed)}


def materialize(artifacts, pristine, destination, policy, keep_symlinks=False):
    """Copy pristine to destination and lay the exported editable roots over it.

    Symlinks inside an exported root are rejected before copytree could follow them,
    or with keep_symlinks copied as links for repair() to undo.
    """
    file_artifacts = (policy.get("operations") == ["modify"] and
                      policy["source_roots"] == ["."] and bool(policy.get("allowed_files")))
    expected = policy["allowed_files"] if file_artifacts else policy["source_roots"]
    extras = set(artifacts) - set(expected)
    if not set(expected) <= set(artifacts) or any(
            not (test_path(name) or test_path(name, source=True)) for name in extras):
        raise ValueError("Artifacts must match the declared editable roots")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Replay destination must be fresh")
    shutil.copytree(pristine, destination, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "node_modules"))
    for root, source in artifacts.items():
        if root in extras:
            relative = Path(root)
            source = Path(source)
            if (relative.is_absolute() or ".." in relative.parts or
                    any(part in (".git", "node_modules", "__pycache__") for part in relative.parts)
                    or source.is_symlink() or any(p.is_symlink() for p in source.parents)):
                raise ValueError("Unsafe added test artifact")
            target = destination / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            elif source.is_dir():
                if not keep_symlinks and any(p.is_symlink() for p in source.rglob("*")):
                    raise ValueError("Symlink in added test artifact")
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target, symlinks=keep_symlinks)
            else:
                raise ValueError("Missing added test artifact")
            continue
        if file_artifacts:
            if not source_policy.allowed(root, policy):
                raise ValueError("Unsafe file artifact")
            source = Path(source)
            if source.is_symlink() or not source.is_file() or any(p.is_symlink() for p in source.parents):
                raise ValueError("File artifact must be a regular, non-symlinked file")
            target = destination / root
            if not target.is_file() or target.is_symlink():
                raise ValueError("Modify-only artifact must replace an existing production file")
            shutil.copyfile(source, target)
            continue
        if root == "." or Path(root).is_absolute() or ".." in Path(root).parts:
            raise ValueError("Unsafe artifact root")
        source = Path(source)
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Artifact root must be a directory")
        # Reject symlinks before copytree follows them.
        if not keep_symlinks and any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("Symlink in artifact")
        target = destination / root
        shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=keep_symlinks)
