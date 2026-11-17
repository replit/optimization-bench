"""Untimed real-image controls: persistent JSONL worker vs fresh-worker outcomes."""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("terser_adapter", HERE / "adapter.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

def source_hash(project):
    digest = hashlib.sha256()
    for path in sorted(Path(project).rglob("*.js")):
        digest.update(str(path.relative_to(project)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()

def execute(cases, image, output, cpuset="0", control=None, project=None, wrapped=True):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    corpus = "".join(json.dumps(adapter.request(x) if wrapped else x, ensure_ascii=True, sort_keys=True,
                               allow_nan=False) + "\n" for x in cases)
    (output / "requests.jsonl").write_text(corpus)
    project = project or "/app/baseline-build/project"
    prepare = ""
    if control:
        project = "/tmp/control"
        prepare = (f"cp -a /app/terser {project}; node /verifier/control.mjs {project} {control}; "
                   f"cd {project}; npm run build >&2; ")
    command = [
        "docker", "run", "--rm", "-i", "--network", "none", "--read-only",
        "--cpuset-cpus", cpuset, "--memory", "16g", "--pids-limit", "512",
        "--tmpfs", "/tmp:rw,exec,size=2g", "--mount", f"type=bind,src={HERE},dst=/verifier,readonly",
        "--mount", f"type=bind,src={output},dst=/observations", image,
        "sh", "-euc", prepare +
        f"build_sha=$(node /verifier/source-identity.mjs {project} /observations/build-identity.json); "
        f"exec node /verifier/driver.mjs --project {project} --build-sha256 \"$build_sha\" "
        "3>/observations/observations.jsonl",
    ]
    process = subprocess.run(command, input=corpus, text=True, capture_output=True,
                             timeout=max(180, 20 * len(cases)))
    (output / "stdout.txt").write_text(process.stdout)
    (output / "stderr.txt").write_text(process.stderr)
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    if process.returncode:
        raise RuntimeError(f"Invalid runner {process.returncode}; inspect {output}/stderr.txt")
    observations = [json.loads(line) for line in (output / "observations.jsonl").read_text().splitlines()]
    if len(observations) != len(cases):
        raise RuntimeError("Malformed/missing independent observer response")
    assert len({x["diagnostics"]["worker_pid"] for x in observations}) == 1
    assert [x["diagnostics"]["request_index"] for x in observations] == list(range(1, len(cases) + 1))
    return observations

def run(output, image):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Freeze before looking at outcomes. Same selected public cases for all cells.
    cases = []
    for mode in ["import", "require"]:
        for api in ["minify", "minify_sync"]:
            cases += [
                adapter.case(adapter.SOURCES[1], mode=mode, api=api),
                adapter.case(adapter.SOURCES[2], {"sourceMap": {"asObject": True}}, mode=mode, api=api),
                adapter.case("if (", mode=mode, api=api),
                adapter.case(adapter.SOURCES[3], {"mangle": {"toplevel": True}}, mode=mode, api=api,
                             sequence=[{"files": adapter.SOURCES[1], "cache": "a"},
                                       {"files": adapter.SOURCES[2], "cache": "b"},
                                       {"files": "if (", "cache": "a"},
                                       {"files": adapter.SOURCES[1], "cache": "a"}]),
            ]
    legacy_spec = importlib.util.spec_from_file_location(
        "terser_legacy_generator", HERE.parent / "differential/generate.py")
    legacy = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy)
    legacy_cases = list(legacy.generate(random.Random(1729), 24))
    cases += [adapter.from_legacy_public(value) for value in legacy_cases if value["kind"] == "minify"]
    (output / "frozen-cases.json").write_text(json.dumps(cases, indent=2, ensure_ascii=True) + "\n")
    fresh = [execute([value], image, output / f"fresh-{index}")[0] for index, value in enumerate(cases)]
    records = []
    # 1/2/8 CPUs x 5 repetitions; every case repeated three times in each one worker.
    for cpuset in ["0", "0-1", "0-7"]:
        for repetition in range(5):
            batch = cases + list(reversed(cases)) + cases
            observations = execute(batch, image, output / f"batch-{cpuset}-{repetition}", cpuset)
            expected = fresh + list(reversed(fresh)) + fresh
            for value, base, observed in zip(batch, expected, observations):
                comparison = adapter.compare(base, observed, value)
                if not comparison["equal"]:
                    raise RuntimeError(f"Baseline persistent/fresh mismatch {cpuset}/{repetition}: {comparison}")
                for step in observed["effects"]["steps"]:
                    state = step["lifecycle"]
                    assert not state["runtime_configuration_changed"]
                    assert all(x["available"] and x["added_count"] == x["removed_count"] == 0
                               for k, x in state.items() if k != "runtime_configuration_changed")
            records.append({"cpuset": cpuset, "repetition": repetition,
                            "observations": len(observations), "fresh_equivalent": True})
    controls = []
    witness = adapter.case(adapter.SOURCES[1])
    for control in ["semantic", "state-counter", "fd-leak", "thread-leak"]:
        for repeat in range(2):
            observations = execute([witness, witness, witness], image,
                                   output / f"control-{control}-{repeat}", control=control)
            comparison = adapter.compare(fresh[0], observations[0], witness)
            assert not comparison["equal"], "Public semantic control must differ"
            state_change = not adapter.compare(observations[0], observations[1], witness)["equal"]
            if control == "state-counter":
                assert state_change, "Persistent-state control must differ on repetition"
            controls.append({"id": control, "fresh_replay": repeat, "compiled": True,
                             "public_semantic_difference": comparison,
                             "repeat_difference": state_change})
    result = {
        "format": "optimbench-fuzz-v1", "task": "terser-minify",
        "profile": "initial-generic-persistent-worker-control", "image": image,
        "case_count": len(cases), "batch_matrix": records, "controls": controls,
        "legacy_public_cases_retained": sum(x["kind"] == "minify" for x in legacy_cases),
        "legacy_internal_walker_cases": {
            "count": sum(x["kind"] != "minify" for x in legacy_cases),
            "status": "not silently admitted as public; original differential gate retained unchanged, API-scope review pending",
        },
        "timing_performed": False, "claimable": False,
        "qualification": "not full task qualification; corrected surface and deep campaigns pending",
        "build_identity_note": "Independent source/build file SHA manifests captured before worker import, with immutable image/lock binding",
        "memory": {"mode": "report-only", "available": False,
                   "reason": "This initial worker smoke does not create per-unit peak cgroups", "verdict_impact": False},
    }
    raw = json.dumps(result, sort_keys=True, indent=2) + "\n"
    (output / "summary.json").write_text(raw)
    print(raw)
    print("summary_sha256", hashlib.sha256(raw.encode()).hexdigest())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.out, args.image)
