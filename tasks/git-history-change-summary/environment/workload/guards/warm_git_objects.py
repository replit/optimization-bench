#!/usr/bin/env python3
"""Task-owned untimed preparation; read canonical pack data, never execute candidate Git.

Original diff-tree units only pre-read their canonical packs. Later units may
also carry a protected derive-plan.json, written by the adapter into fresh case
scratch; derive_inputs.py applies it with the image's pristine Git."""
import argparse
import json
import os
from pathlib import Path
import stat

BUFFER_BYTES = 8 * 1024 * 1024


def selected_stores(request, inputs):
    """The runner's protected step request selects repositories, not the build."""
    if isinstance(request, dict):
        request = request["steps"]
    if not isinstance(request, list):
        raise ValueError("Expected a protected step list")
    inputs = Path(inputs).resolve(strict=True)
    histories = inputs / "histories"
    repositories = set()
    for step in request:
        argv = step["argv"]
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise ValueError("Invalid protected step arguments")
        if "diff-tree" not in argv:
            continue
        if "--stdin" not in argv or argv.count("-C") != 1:
            raise ValueError("Expected the task's canonical repository batch")
        index = argv.index("-C") + 1
        if index == len(argv):
            raise ValueError("Missing repository argument")
        repository = Path(argv[index])
        if not repository.is_absolute() or repository.parent != histories:
            raise ValueError("Repository outside the protected histories root")
        if repository.is_symlink() or repository.resolve(strict=True) != repository:
            raise ValueError("Repository paths must be canonical and not symlinked")
        repositories.add(repository)
    stores = []
    for repository in sorted(repositories):
        pack = repository / ".git/objects/pack"
        if pack.resolve(strict=True) != pack:
            raise ValueError("Object store must not contain symlinked parents")
        packs = sorted(pack.glob("*.pack"))
        indices = sorted(pack.glob("*.idx"))
        if not packs or {x.stem for x in packs} != {x.stem for x in indices}:
            raise ValueError("Expected canonical paired pack and index files")
        stores.extend(sorted(packs + indices))
    return len(repositories), stores


def prepare(request, inputs):
    repositories, files = selected_stores(request, inputs)
    buffer = bytearray(BUFFER_BYTES)
    total = 0
    for path in files:
        # Do not follow object-file symlinks, including a replacement between
        # selection and open. Runtime inputs are also mounted read-only.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Expected a regular canonical object file")
            count = 0
            while size := stream.readinto(buffer):
                count += size
            after = os.fstat(stream.fileno())
            if (count != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ValueError("Canonical input changed during preparation")
            total += count
    return {
        "status": "prepared" if repositories else "no-repository-steps",
        "selected_repositories": repositories,
        "files_read": len(files),
        "bytes_read": total,
        "buffer_bytes": BUFFER_BYTES,
    }


def derived(request, inputs):
    """Apply an adapter-written plan from the protected case scratch, if any."""
    if not isinstance(request, dict) or "output" not in request:
        return None
    plan = Path(request["output"]) / "derive-plan.json"
    if not plan.exists():
        return None
    if plan.is_symlink() or not plan.is_file():
        raise ValueError("Expected a regular derivation plan")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import derive_inputs
    return derive_inputs.apply(json.loads(plan.read_text()), request["output"], inputs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, default=Path("/app/workload/inputs"))
    args = parser.parse_args()
    # The generic judge hook, not this helper, owns untimed execution and
    # captures this receipt separately from all workload observations.
    request = json.loads(args.steps.read_text())
    receipt = prepare(request, args.inputs)
    extra = derived(request, args.inputs)
    if extra is not None:
        receipt["derived"] = extra
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
