"""Reproduce the narrow XZ memory oracle + two mandatory source controls.

This is a local/fresh-process control trial, not full v1.1 qualification.
For task qualification the task owner replays these in fresh pinned workers
and supplies the rest of the >=20 reviewed, compiled non-equivalent portfolio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
from source_controls import apply_recipe, source_identity
from controls import recipes, PINNED_COMMIT, PINNED_ARCHIVE_SHA256, PINNED_SOURCE_IDENTITY
from oracle import read_result


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(argv, cwd=None, timeout=600):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv}\n"
                           + result.stderr.decode("utf-8", "replace")[-4000:])
    return result.stdout


def build(source, out):
    cmake = out / "cmake"
    flags = "-O2 -g -DNDEBUG"
    log = execute(["cmake", "-S", str(source), "-B", str(cmake), "-G", "Ninja",
                   "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_C_FLAGS_RELEASE={flags}",
                   "-DBUILD_TESTING=ON"])
    log += execute(["cmake", "--build", str(cmake), "--target", "liblzma", "-j8"])
    binary = out / "memusage-probe"
    log += execute(["cc", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
                    "-I" + str(source / "src/liblzma/api"), str(HERE / "probe.c"),
                    str(cmake / "liblzma.a"), "-lpthread", "-o", str(binary)])
    (out / "build.log").write_bytes(log)
    return binary, {"library_sha256": digest(cmake / "liblzma.a"),
                    "probe_sha256": digest(binary), "library_flags": flags,
                    "build_log_sha256": digest(out / "build.log")}


def observe(binary, mode, out, input_bytes, repetition=0, preset=0, threads=2):
    args = [mode, str(preset), str(threads), str(input_bytes), "4096", "3072"]
    path = out / f"{mode}-p{preset}-n{input_bytes}-r{repetition}.jsonl"
    result = subprocess.run([str(binary), *args], capture_output=True, timeout=120)
    path.write_bytes(result.stdout)
    path.with_suffix(".stderr").write_bytes(result.stderr)
    evaluated = read_result(path, result.returncode)
    return {"case": {"mode": mode, "preset": preset, "threads": threads,
                     "input_bytes": input_bytes, "input_chunk": 4096, "output_chunk": 3072},
            "transcript": path.name, "transcript_sha256": digest(path),
            "exit_code": result.returncode, "verdict": evaluated["verdict"],
            "findings": evaluated.get("findings", []),
            "peak_owned_bytes": evaluated.get("peak_owned_bytes"),
            "allocation_count": evaluated.get("allocation_count"),
            "reason": evaluated.get("reason")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True,
                   help="clean extracted pinned XZ archive, not any sweep candidate")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(source) or source.is_relative_to(out):
        p.error("output must be new and disjoint from source")
    out.mkdir(parents=True)
    source_hash = source_identity(source)
    if source_hash != PINNED_SOURCE_IDENTITY:
        raise RuntimeError("source differs from the verified pristine archive tree")
    report = {
        "schema": "xz-memusage-validation-v1", "scope": "seven API modes; local control trial",
        "qualification": "undertested", "full_task_mutation_minimum_met": False,
        "isolation": "fresh-process (not fresh pinned task containers)",
        "upstream_commit": PINNED_COMMIT, "archive_sha256": PINNED_ARCHIVE_SHA256,
        "source_sha256": source_hash, "probe_source_sha256": digest(HERE / "probe.c"),
        "oracle_source_sha256": digest(HERE / "oracle.py"),
        "controls_source_sha256": digest(HERE / "controls.py"),
        "host": platform.platform(),
        "compiler": execute(["cc", "--version"]).decode().splitlines()[0],
        "cmake": execute(["cmake", "--version"]).decode().splitlines()[0],
        "memory_caps": {"enforced": False, "ratio_verdict_impact": False},
    }
    pristine = out / "pristine"
    pristine.mkdir()
    binary, report["pristine_build"] = build(source, pristine)
    matrix = []
    for mode in ("raw-encode", "easy-encode", "stream-encode", "mt-encode",
                 "raw-decode", "stream-decode", "easy-decode"):
        for size in (0, 1, 65535, 65536, 65537, 131073):
            for rep in range(2):
                matrix.append(observe(binary, mode, pristine, size, rep))
    report["pristine"] = matrix
    if any(r["verdict"] != "passed" for r in matrix):
        save(out / "validation.json", report)
        raise RuntimeError("pristine oracle/precondition failure; no candidate verdict")
    controls = []
    for recipe in recipes(source):
        work = out / recipe["id"].split(":")[-1]
        work.mkdir()
        mutant = work / "source"
        shutil.copytree(source, mutant, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        changes = apply_recipe(mutant, recipe)
        save(work / "recipe.json", recipe)
        mutant_binary, identity = build(mutant, work)
        full = [observe(mutant_binary, "raw-encode", work, 131073, r) for r in range(2)]
        # Minimal valid input is empty, preserving API/options/lifecycle/failure.
        minimal = [observe(mutant_binary, "raw-encode", work, 0, r) for r in range(2)]
        rows = full + minimal
        control = {"id": recipe["id"], "operator_class": recipe["operator_class"],
                   "compiled": True, "changes": changes, "build": identity,
                   "source_sha256": source_identity(mutant),
                   "non_equivalence_evidence": recipe["non_equivalence_evidence"],
                   "status": "killed-in-local-control-trial" if all(
                       r["verdict"] == "contract_regression" and
                       any(f["category"] == "underreported-workspace" for f in r["findings"])
                       for r in rows) else "unresolved",
                   "original": full, "minimized": minimal,
                   "minimization": {"preserved": ["API", "options", "lifecycle", "underreported-workspace"],
                                    "lower_input_bound": 0, "input_bytes_before": 131073,
                                    "input_bytes_after": 0},
                   "reviewed_by": None, "fresh_container_replay": False}
        controls.append(control)
        save(work / "reproducer.json", control)
    report["controls"] = controls
    report["compiled_controls"] = len(controls)
    report["controls_killed_locally"] = sum(c["status"] == "killed-in-local-control-trial" for c in controls)
    if source_identity(source) != source_hash:
        raise RuntimeError("pristine source modified")
    save(out / "validation.json", report)
    print(json.dumps({"receipt": str(out / "validation.json"),
                      "sha256": digest(out / "validation.json"),
                      "pristine_trials": len(matrix),
                      "local_controls_killed": report["controls_killed_locally"],
                      "full_task_qualification": "undertested"}, sort_keys=True))


if __name__ == "__main__":
    main()
