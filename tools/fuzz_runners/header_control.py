"""Reproducible GNU-inline two-TU calibration; no optimizer/task patch input."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .header_run import Context, DockerExecutor, HERE, Step, run_pair
from .receipt import sha256

HEADER = """#ifndef PUBLIC_CONTROL_H
#define PUBLIC_CONTROL_H
#ifdef __cplusplus
extern "C" {
#endif
int library_value(int);
#ifdef __cplusplus
}
#endif
static inline __attribute__((always_inline)) int public_inline(int n) {
    return library_value(n) + 1;
}
#endif
"""
TU1 = '#include "public.h"\nint first(void) { return public_inline(1); }\n'
TU2 = '#include "public.h"\nint first(void);\nint main(void) { return first() + public_inline(2) == 5 ? 0 : 1; }\n'


def control_manifest():
    support = HERE / "fixtures/header-support.txt"
    refs = [{"path": "tools/fuzz_runners/fixtures/header-support.txt",
             "sha256": sha256(support), "quote": support.read_text().strip()}]
    cells = []
    for dialect, language in (("gnu89", "c"), ("c99", "c"), ("c++11", "c++")):
        cells.append({
            "id": "public-" + dialect.replace("+", "p"), "supported": True,
            "headers": ["public.h"], "language": language, "dialect": dialect,
            "features": [], "support_refs": refs, "include_context": {"id": "public-direct", "includes": ["public.h"]},
            "compiler": "clang-16" if language == "c" else "clang++-16",
            "include_dirs": ["."], "libraries": ["libcontrol.a"], "defines": [],
            "compile_flags": [], "link_flags": [], "symbol_refs": ["public_inline"],
            "translation_units": [{"name": "first.c" if language == "c" else "first.cc", "source": TU1},
                                  {"name": "main.c" if language == "c" else "main.cc", "source": TU2}]})
    cells.append({"id": "public-cpp03", "supported": False, "reason": "outside fixture's declared calibration dialects",
                  "support_refs": refs})
    return {"schema": "optimbench-header-matrix-v1", "scope": "runner-control-only",
            "required_cells": [c["id"] for c in cells], "cells": cells}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--parent-image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise ValueError("use a fresh output directory")
    out.mkdir(parents=True)
    baseline, candidate = out / "baseline-build", out / "candidate-build"
    for root, header in ((baseline, HEADER), (candidate, HEADER.replace("static inline", "inline"))):
        root.mkdir()
        (root / "public.h").write_text(header)
        (root / "library.c").write_text("int library_value(int n) { return n; }\n")
    scratch = out / "prepare"
    scratch.mkdir()
    ctx = Context(scratch=scratch, build=baseline, inputs=baseline, projects=baseline, workload=baseline, docker=True)
    image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    executor = DockerExecutor(image_id)
    steps = executor.run([
        Step(["clang-16", "-std=c99", "-O0", "-fno-lto", "-c", "/app/build/library.c", "-o", "/scratch/library.o"]),
        Step(["ar", "rcsD", "/scratch/libcontrol.a", "/scratch/library.o"])], ctx, {})
    if any(s.exit_code != 0 for s in steps):
        raise RuntimeError("control library preparation failed")
    for root in (baseline, candidate):
        shutil.copy(scratch / "libcontrol.a", root / "libcontrol.a")
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(control_manifest(), indent=2) + "\n")
    def tree_hash(root):
        return hashlib.sha256(json.dumps({p.name: sha256(p) for p in sorted(root.iterdir())}, sort_keys=True).encode()).hexdigest()
    result = run_pair(
        image_ref=image_id, parent_image=args.parent_image, baseline=baseline, candidate=candidate,
        manifest=manifest, out=out / "matrix", baseline_source_sha256=sha256(baseline / "public.h"),
        candidate_source_sha256=sha256(candidate / "public.h"),
        baseline_build_sha256=tree_hash(baseline), candidate_build_sha256=tree_hash(candidate))
    observed = {c["id"]: c["attribution"] for c in result["cells"]}
    expected = {"public-gnu89": "candidate_compatibility_difference", "public-c99": "same_success",
                "public-cpp11": "same_success", "public-cpp03": "unsupported"}
    if observed != expected:
        raise RuntimeError("header calibration did not demonstrate declared outcomes: " + json.dumps(observed))
    # A failure at compile instead of link is not the intended inline control.
    gnu = next(c for c in result["cells"] if c["id"] == "public-gnu89")
    if not gnu["candidate_compile_succeeded"] or gnu["candidate_link_succeeded"]:
        raise RuntimeError("GNU89 control must compile twice and fail only at link")
    print(json.dumps(result["cells"], indent=2))


if __name__ == "__main__":
    main()
