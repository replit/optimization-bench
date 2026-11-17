#!/usr/bin/env python3
"""Negative provenance controls. Uses public original source, never held-out data."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from native_provenance import authenticate_source, verify_build, WORKLOAD


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original-build", type=Path, required=True)
    p.add_argument("--image", required=True)
    args = p.parse_args()
    verify_build(args.original_build)
    with tempfile.TemporaryDirectory(prefix="native-provenance-controls-") as directory:
        root = Path(directory)
        copied = root / "copied"
        shutil.copytree(args.original_build, copied, ignore=shutil.ignore_patterns("go-cache"))
        binary = copied / "sqlfmt-native"
        binary.write_bytes(b"substituted executable")
        try:
            verify_build(copied)
        except ValueError as error:
            assert "executable" in str(error)
        else:
            raise AssertionError("Substituted executable was accepted")
        source = root / "source"
        shutil.copytree(args.original_build / "project", source)
        path = source / "format/lexer.go"
        original = path.read_bytes()
        path.write_bytes(original + b"\n// changed original source\n")
        try:
            authenticate_source(source)
        except ValueError:
            pass
        else:
            raise AssertionError("Editable formatter source escaped original authentication")
        path.write_bytes(original)
        probe = source / "format/native_stale_probe.go"
        probe.write_text("package format\nfunc nativeStaleProbe() {}\n")
        build = root / "reused"
        build.mkdir()
        command = [
            "docker", "run", "--rm", "--network", "none", "--cpuset-cpus", "0-7",
            "-v", f"{source}:/candidate:ro", "-v", f"{build}:/build",
            "-v", f"{WORKLOAD}:/app/workload:ro", "-e", "OPTIMBENCH_SOURCE=/candidate",
            args.image, "sh", "/app/workload/build.sh", "/build",
        ]
        for phase in range(2):
            with (root / f"build-{phase}.log").open("wb") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            assert result.returncode == 0, "Reused build failed"
            if phase == 0:
                assert (build / "project/format/native_stale_probe.go").is_file()
                probe.unlink()
                (build / "native-driver/stale.go").write_text("this is not Go source")
            else:
                assert not (build / "project/format/native_stale_probe.go").exists()
                assert not (build / "native-driver/stale.go").exists()
                verify_build(build)
        print(json.dumps({
            "substituted_executable_rejected": True,
            "modified_editable_original_source_rejected": True,
            "deleted_source_not_retained_on_rebuild": True,
            "stale_driver_not_retained_on_rebuild": True,
            "complete_original_inventory_authenticated": True,
        }, indent=2))


if __name__ == "__main__":
    main()
