"""Bind freshly built executables to copied source, protected caller and recipe."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError("Source symlinks are not accepted")
        if path.is_file():
            result[relative.as_posix()] = digest(path)
    return result


def main(build, workload):
    receipt = {
        "schema": "sqlfmt-rebuilt-source-v1",
        "source_files_sha256": source_files(build / "project"),
        "driver_sha256": digest(workload / "native/main.go"),
        "recipe_sha256": digest(workload / "build.sh"),
        "toolchain": subprocess.check_output(["go", "version"], text=True).strip(),
        "flags": ["-trimpath"], "cgo_enabled": False,
        "binaries": {name: digest(build / name) for name in ("sqlfmt", "sqlfmt-native")},
    }
    (build / "native-build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
