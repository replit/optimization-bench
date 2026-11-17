#!/usr/bin/env python3
"""Real Node subprocess observations; shared judge assigns every verdict."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parent
# Standalone entry and private task-loader import both use relative imports.
if not __package__:
    package = types.ModuleType("_terser_rc")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    __package__ = package.__name__
from .adapter import request, digest
from .rc_cases import cases


def inventory(project):
    paths = sorted([*project.glob("lib/**/*.js"),
                    *(project / name for name in ("main.js", "package.json",
                       "package-lock.json", "tools/terser.d.ts", "dist/bundle.min.js"))])
    return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def collect(baseline, candidate, builds, out, node="node"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    node = shutil.which(node)
    if not node:
        raise ValueError("Node executable unavailable")
    values = cases()
    surfaces = {value["id"]: value for value in
                json.loads((ROOT / "surface.json").read_text())["entries"]}
    projects = {"baseline": Path(baseline) / "project", "candidate": Path(candidate) / "project"}
    identities = {arm: inventory(project) for arm, project in projects.items()}
    fresh, persistent, workers = {}, {}, {}

    def execute(arm, tag, inputs):
        raw_path = out / (arm + "-" + tag + ".jsonl")
        input_text = "".join(json.dumps(request(value)) + "\n" for value in inputs)
        (out / (arm + "-" + tag + "-cases.jsonl")).write_text(input_text)
        with raw_path.open("wb") as raw:
            command = [node, str(ROOT / "driver.mjs"), "--project", str(projects[arm]),
                       "--build-sha256", builds[arm], "--observer-fd", str(raw.fileno())]
            run = subprocess.run(command, input=input_text.encode(), capture_output=True,
                                 pass_fds=(raw.fileno(),), timeout=120)
        (out / (arm + "-" + tag + ".stdout")).write_bytes(run.stdout)
        (out / (arm + "-" + tag + ".stderr")).write_bytes(run.stderr)
        if run.returncode or run.stdout or run.stderr:
            raise ValueError("Terser worker transport failed; raw streams retained")
        observations = [json.loads(line) for line in raw_path.read_text().splitlines()]
        if len(observations) != len(inputs):
            raise ValueError("Lost or excess worker observations")
        for index, (value, observed) in enumerate(zip(inputs, observations)):
            if (observed["identity"]["case_sha256"] != digest(value)
                    or observed["identity"]["build_sha256"] != builds[arm]
                    or observed["diagnostics"]["request_index"] != index + 1):
                raise ValueError("Worker request/build identity mismatch")
        return observations

    for arm in builds:
        fresh[arm] = [[execute(arm, f"fresh-{index}-{replay}", [value])[0]
                       for replay in range(2)] for index, value in enumerate(values)]
        # Reverse the second pass to expose order-dependent persistent state.
        persistent[arm] = execute(arm, "persistent", values + list(reversed(values)))
        first = persistent[arm][0]["diagnostics"]["lifecycle"]["before"]
        main = next(item for item in first["thread_identities"] if item["tid"] == first["scope_pid"])
        if any(item["diagnostics"]["worker_pid"] != main["tid"] for item in persistent[arm]):
            raise ValueError("Persistent worker identity changed")
        workers[arm] = {"mode": "persistent", "target_library_in_process": True,
                        "worker_id": f"{main['tid']}:{main['start_ticks']}",
                        "calls": len(persistent[arm]), "build_sha256": builds[arm]}
    rows, repeat, lifecycle = [], [], []
    for index, value in enumerate(values):
        positions = (index, 2 * len(values) - 1 - index)
        rows.append({"case": value, "surface": surfaces[value["surface_id"]],
                     **{arm: fresh[arm][index] for arm in builds}})
        repeat.append({"case": value, "surface": surfaces[value["surface_id"]],
                       **{arm: [persistent[arm][pos] for pos in positions] for arm in builds},
                       **{"fresh_" + arm: fresh[arm][index] for arm in builds}})
        for replay, pos in enumerate(positions):
            lifecycle.append({"case_id": digest(value), "repetition": replay,
                **{arm: persistent[arm][pos]["diagnostics"]["lifecycle"] for arm in builds}})
    if identities != {arm: inventory(project) for arm, project in projects.items()}:
        raise ValueError("Source/build files changed during observations")
    common = {"format": "judge-generic-call-evidence-v1", "applicable": True}
    facts = {
        "repeat-facts.json": {**common, "cases": repeat, "workers": workers, "lifecycle": lifecycle},
        "alias-facts.json": {**common, "cases": rows[::3]},
        "stream-facts.json": {**common, "cases": [row for i, row in enumerate(rows) if i % 3],
            "direct_reader_writer_scope": {
                "scope": "Documented minify APIs accept source strings/maps, not Reader/Writer objects. "
                         "The actual format.comments callback throws EPIPE/ENOSPC after one/two calls, "
                         "retains partial callback events and retries in the same module.",
                "authority": json.loads((ROOT / "surface.json").read_text())["evidence"],
                "not_claimed": "OS stream coverage belongs to the separate actual CLI matrix."}},
        "build-receipt.json": {"builds": builds, "source_files": identities,
                              "node": node, "node_sha256": hashlib.sha256(Path(node).read_bytes()).hexdigest(),
                              "controller_affinity": sorted(os.sched_getaffinity(0))},
    }
    for name, value in facts.items():
        (out / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    return facts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "baseline-sha", "candidate-sha", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    collect(args.baseline, args.candidate,
            {"baseline": args.baseline_sha, "candidate": args.candidate_sha}, args.out, args.node)
