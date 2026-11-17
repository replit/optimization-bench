"""Package small, content-bound *runner calibration* evidence, never task scores."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile

from .receipt import artifact, build_receipt, coverage_receipt, sha256


def collect(smoke, image_records, out):
    if out.exists():
        raise ValueError("use a fresh evidence directory")
    out.mkdir(parents=True)
    cap = json.loads((smoke / "capabilities.json").read_text())
    if cap.get("status") == "invalid_run" or not cap.get("runtime_input_sha256"):
        raise ValueError("complete probe plus runtime-input commitments required")
    (out / "capabilities.json").write_text(json.dumps(cap, sort_keys=True, indent=2) + "\n")
    images = [json.loads(path.read_text()) for path in image_records]
    if not any(item.get("runner_image") == cap["runner_image"] for item in images):
        raise ValueError("runtime image is not bound by provided build records")
    (out / "images.json").write_text(json.dumps(images, sort_keys=True, indent=2) + "\n")
    probe = smoke / "scratch/probe"
    # Raw logs/exports, plus exact generated source/templates. Deliberately omit
    # dependency caches and compiled binaries; hashes of consumed binaries remain
    # in runtime artifacts below and rebuilding uses the pinned recipes.
    names = []
    for path in probe.glob("step-*.*"):
        names.append(path)
    work = probe / "work"
    for path in work.rglob("*"):
        rel = path.relative_to(work)
        if not path.is_file() or any(x in rel.parts for x in
                                    ("target", "cargo-home", "go-cache", "tmp", "branch-hooks", "afl-output")):
            continue
        if path.suffix in (".json", ".c", ".cjs", ".go", ".py", ".rs", ".toml", ".lock", ".rc") or path.name == "go-cover.txt":
            names.append(path)
    with (out / "raw-evidence.tar.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(set(names)):
                    content = path.read_bytes()
                    info = tarfile.TarInfo(path.relative_to(probe).as_posix())
                    info.size, info.mtime, info.mode = len(content), 0, 0o644
                    archive.addfile(info, io.BytesIO(content))
    exports = {}
    for name in ("coverage-normalized.json", "child-coverage-normalized.json"):
        path = work / name
        if path.exists():
            shutil.copy(path, out / name)
            exports[name] = artifact(out / name, out)
    binaries = {}
    for path in work.rglob("*"):
        if not path.is_file() or any(x in path.relative_to(work).parts for x in
                                    ("cargo-home", "go-cache", "tmp", "afl-output")):
            continue
        with path.open("rb") as stream:
            if stream.read(4) == b"\x7fELF":
                binaries[path.relative_to(work).as_posix()] = sha256(path)
    manifest = {
        "schema": "optimbench-runner-calibration-v1", "scope": "synthetic-runtime-controls-only",
        "language": cap["language"], "runner_image": cap["runner_image"],
        "runtime_inputs": cap["runtime_input_sha256"],
        "image_builds": artifact(out / "images.json", out),
        "capabilities": artifact(out / "capabilities.json", out),
        "raw_evidence": artifact(out / "raw-evidence.tar.gz", out),
        "normalized_exports": exports, "compiled_artifacts_sha256": binaries,
        "task_qualification": False, "task_coverage_or_mutation_score": None,
        "limitations": "Runtime controls validate tool feasibility only. Task source/build/features, complete dependency instrumentation, changed target denominators, all workers and mutation adequacy need separate canonical receipts.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    if cap["language"] == "native":
        control = cap["capabilities"]["llvm-coverage"]
        command = cap["steps"][control["evidence"][0]]["argv"]
        sample = build_receipt(
            language="c", runner_image=cap["runner_image"],
            parent_task_image=images[0]["parent_task_image"],
            source_sha256=sha256(work / "native.c"), build_sha256=sha256(work / "covered"),
            features=[], instrumentation="llvm-source-coverage",
            flags=command[1:command.index("native.c")],
            tools={"scope": "single synthetic native.c control, not a task build",
                   "compile_argv": command, "capability_receipt": artifact(out / "capabilities.json", out),
                   "compiler_version_evidence": cap["steps"][0]},
            capabilities={"llvm-source-coverage": control},
            artifacts=[artifact(out / "raw-evidence.tar.gz", out)])
        sample_path = out / "sample-build-receipt.json"
        sample_path.write_text(json.dumps(sample, sort_keys=True, indent=2) + "\n")
        coverage = coverage_receipt(
            build_receipt_sha256=sha256(sample_path), source_sha256=sha256(work / "native.c"),
            raw_paths=[work / "llvm.json"], normalized_path=work / "coverage-normalized.json",
            base=probe, expected_workers=["synthetic-main"], observed_workers=["synthetic-main"])
        (out / "sample-coverage-receipt.json").write_text(json.dumps(coverage, sort_keys=True, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--image-record", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = collect(args.smoke, args.image_record, args.out)
    print(json.dumps({"language": data["language"], "raw_evidence": data["raw_evidence"]}))


if __name__ == "__main__":
    main()
