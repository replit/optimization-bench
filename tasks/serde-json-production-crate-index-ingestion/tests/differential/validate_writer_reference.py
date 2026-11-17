#!/usr/bin/env python3
"""Untimed reference validation inside the pinned pristine image; no sweep inputs.

docker run --network none ... python3 /verifier-differential/validate_writer_reference.py --out /scratch
Produces a frozen generated corpus, pristine repeatability evidence, disposable
pristine-source mutants, kill/minimization receipts. This is NOT task adequacy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

from writer_faults import case_at, semantic, shrink

EXTERNAL_COMMAND_CONTEXTS = {
    "compile_probe": "pinned-worker-verifier-observer",
    "observe": "pinned-worker-verifier-observer",
    "main": "pinned-worker-verifier-observer",
}

ENV = {**os.environ, "RUSTUP_HOME": "/usr/local/rustup", "RUSTUP_TOOLCHAIN": "1.93.1"}
FOLDER = Path(__file__).resolve().parent
ORIGINAL = "    let mut ser = Serializer::new(writer);\n    value.serialize(&mut ser)\n"
MUTANTS = {
    "writer-error-panics": "    let mut ser = Serializer::new(writer);\n"
                          "    match value.serialize(&mut ser) { Ok(()) => Ok(()), Err(_) => panic!(\"curated IO panic\") }\n",
    "writer-error-swallowed": "    let mut ser = Serializer::new(writer);\n"
                             "    let _ = value.serialize(&mut ser);\n    Ok(())\n",
    "writer-output-discarded": "    let mut ser = Serializer::new(std::io::sink());\n"
                              "    value.serialize(&mut ser)\n",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def compile_probe(build, binary):
    deps = build / "target/release/deps"
    libs = {}
    for name in ("serde", "serde_json"):
        values = list(deps.glob("lib" + name + "-*.rlib"))
        if len(values) != 1:
            raise ValueError("Ambiguous/missing admitted library: " + name)
        libs[name] = values[0]
    command = ["/usr/local/cargo/bin/rustc", "--edition=2021", "-O",
               "-L", "dependency=" + str(deps)]
    for name, path in libs.items():
        command += ["--extern", name + "=" + str(path)]
    command += [str(FOLDER / "writer_probe.rs"), "-o", str(binary)]
    result = subprocess.run(command, env=ENV, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Probe compilation infrastructure failure: " + result.stderr.decode())
    return {"command": command, "binary_sha256": sha(binary.read_bytes()),
            "libraries": {name: sha(path.read_bytes()) for name, path in libs.items()}}


def observe(binary, case):
    result = subprocess.run([str(binary)], input=json.dumps(case).encode(),
                            capture_output=True, timeout=10)
    return {"exit_code": result.returncode, "stderr_class": "nonempty" if result.stderr else "empty",
            "output": semantic(json.loads(result.stdout)) if result.returncode == 0 else None}


def signature(left, right):
    if (left["exit_code"], left["stderr_class"]) != (right["exit_code"], right["stderr_class"]):
        return ("transport",)
    changed = []
    for a, b in zip(left["output"]["observations"], right["output"]["observations"]):
        for key in ("outcome", "written_hex"):
            if a[key] != b[key]:
                changed.append((a["surface"], key))
    return tuple(changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pristine", type=Path, default=Path("/app/baseline-build"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    baseline = args.out / "pristine-probe"
    report = {"format": "optimbench-writer-reference-validation-v1", "timing_performed": False,
              "scope": "four public serialization entrypoints / writer-fault slice only",
              "task_adequacy": "undertested", "seed_list": [1729, 2718, 31415],
              "pristine_build": compile_probe(args.pristine, baseline)}
    corpus = []
    for seed in report["seed_list"]:
        rng = random.Random(seed)
        for index in range(30):
            corpus.append({"seed": seed, "index": index, "case": case_at(index, rng)})
    raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in corpus).encode()
    (args.out / "corpus.jsonl").write_bytes(raw)
    report["corpus"] = {"sha256": sha(raw), "cases": len(corpus), "file": "corpus.jsonl"}
    expected = []
    for row in corpus:
        left, repeat = observe(baseline, row["case"]), observe(baseline, row["case"])
        if left != repeat or left["exit_code"] != 0:
            raise ValueError("Pristine reference not repeatable")
        if any(o["outcome"]["termination"] == "panic" for o in left["output"]["observations"]):
            raise ValueError("Pristine writer probe panicked")
        expected.append(left)
    save(args.out / "pristine-observations.json", expected)
    report["pristine"] = {"cases": len(expected), "entrypoint_observations": len(expected) * 4,
                          "repetitions": 2, "matched": True,
                          "observations_sha256": sha((args.out / "pristine-observations.json").read_bytes())}
    mutants = []
    pristine_ser = Path("/app/serde-json/src/ser.rs").read_text()
    if pristine_ser.count(ORIGINAL) != 1:
        raise ValueError("Pristine mutator anchor is not unique")
    for name, replacement in MUTANTS.items():
        work = args.out / name
        work.mkdir()
        source = work / "source"
        shutil.copytree("/app/serde-json", source, ignore=shutil.ignore_patterns(".git", "target"))
        (source / "src/ser.rs").write_text(pristine_ser.replace(ORIGINAL, replacement, 1))
        built = work / "build"
        result = subprocess.run(["sh", "/app/workload/build.sh", str(built)],
            capture_output=True, env={**ENV, "OPTIMBENCH_SOURCE": str(source)}, timeout=300)
        (work / "build.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            mutants.append({"id": name, "compiled": False, "killed": False,
                            "invalid_reason": "build failed; not counted as a kill"})
            continue
        binary = work / "probe"
        identity = compile_probe(built, binary)
        found = None
        for row, original in zip(corpus, expected):
            observed = observe(binary, row["case"])
            difference = signature(original, observed)
            if difference:
                minimal = row["case"]
                for smaller in shrink(minimal):
                    a, b = observe(baseline, smaller), observe(binary, smaller)
                    if signature(a, b) == difference:
                        minimal = smaller
                a, b = observe(baseline, minimal), observe(binary, minimal)
                if a != observe(baseline, minimal) or b != observe(binary, minimal):
                    raise ValueError("Mutant failure did not reproduce")
                receipt = {"mutant": name, "seed": row["seed"], "original_case": row["case"],
                    "minimized_case": minimal, "mismatch_signature": difference,
                    "baseline": a, "mutant_observation": b, "fresh_process_replay_matched": True}
                save(work / "reproducer.json", receipt)
                found = {"id": name, "compiled": True, "killed": True, "probe": identity,
                         "reproducer": name + "/reproducer.json",
                         "reproducer_sha256": sha((work / "reproducer.json").read_bytes())}
                break
        mutants.append(found or {"id": name, "compiled": True, "killed": False})
    report["mutants"] = mutants
    valid = [m for m in mutants if m["compiled"]]
    report["reference_slice_mutation_score"] = {
        "compiled": len(valid), "killed": sum(m["killed"] for m in valid),
        "rate": sum(m["killed"] for m in valid) / len(valid) if valid else None,
        "not_full_task_score": True}
    save(args.out / "validation.json", report)
    print(json.dumps(report, indent=2))
    if len(valid) != len(MUTANTS) or not all(m["killed"] for m in valid):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
