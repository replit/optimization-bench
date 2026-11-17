"""Build admitted source arms and return raw library/lifecycle observations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import rc_cases


def normalize_affinity(observation):
    """Expose the native worker's measured mask, never the requested Docker mask."""
    diagnostics = observation["diagnostics"]
    measured = diagnostics["execution_affinity"]
    cpus = measured.get("cpus")
    pid = measured.get("pid")
    if (measured.get("available") is not True or type(pid) is not int or pid <= 0
            or not isinstance(cpus, list) or not cpus
            or any(type(cpu) is not int or cpu < 0 for cpu in cpus)
            or len(cpus) != len(set(cpus))):
        raise ValueError("Native Go affinity observation is unavailable or malformed")
    lifecycle = diagnostics["call_lifecycle"]
    if any(lifecycle[point].get("scope_pid") != pid for point in ("before", "after")):
        raise ValueError("Affinity and lifecycle observations belong to different Go workers")
    diagnostics["execution_affinity_source"] = measured
    diagnostics["execution_affinity"] = sorted(cpus)
    return observation


def main():
    parser = argparse.ArgumentParser()
    for name in ("baseline", "candidate", "baseline-sha", "candidate-sha", "out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    go = "/usr/local/go/bin/go" if Path("/usr/local/go/bin/go").exists() else shutil.which("go")
    workers, builds = {}, {}
    for arm in ("baseline", "candidate"):
        source = Path(getattr(args, arm)) / "project"
        target = out / (arm + "-worker")
        env = {**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local", "GOPROXY": "off",
               "GOSUMDB": "off", "GOCACHE": str(out / (arm + "-go-cache"))}
        result = subprocess.run([go, "build", "-trimpath", "-o", str(target),
                                 str(Path(__file__).parent / "worker.go")],
                                cwd=source, env=env, capture_output=True, timeout=240)
        (out / (arm + "-build.stdout")).write_bytes(result.stdout)
        (out / (arm + "-build.stderr")).write_bytes(result.stderr)
        result.check_returncode()
        workers[arm] = target
        builds[arm] = {"admitted_build_sha256": getattr(args, arm + "_sha"),
                       "probe_binary_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    def observe(arm, sequence):
        env = {**os.environ, "OPTIMBENCH_ADMITTED_BUILD_SHA256": getattr(args, arm + "_sha")}
        result = subprocess.run([str(workers[arm])], env=env,
            input="".join(json.dumps(case) + "\n" for case in sequence).encode(),
            capture_output=True, timeout=120)
        result.check_returncode()
        values = [normalize_affinity(json.loads(line)) for line in result.stdout.splitlines()]
        assert len(values) == len(sequence)
        return values
    stream, positive, aliases = rc_cases.cases()
    def rows_for(cases):
        rows = []
        for case in cases:
            assert rc_cases.precondition(case, {})["valid"]
            row = {"case": case, "surface": rc_cases.surface(case)}
            for arm in workers:
                row[arm] = [observe(arm, [case])[0] for _ in range(2)]
            rows.append(row)
        return rows
    def save(name, data):
        (out / name).write_text(json.dumps(data, sort_keys=True) + "\n")
    save("stream-facts.json", {"format": "judge-generic-call-evidence-v1",
                              "applicable": True, "cases": rows_for(stream)})
    save("alias-facts.json", {"format": "judge-generic-call-evidence-v1",
                             "applicable": True, "cases": rows_for(aliases)})
    sequence = positive + list(reversed(positive)) + positive
    persistent = {arm: observe(arm, sequence) for arm in workers}
    repeat, lifecycle = [], []
    for case in positive:
        indices = [i for i, item in enumerate(sequence) if item == case]
        row = {"case": case, "surface": rc_cases.surface(case)}
        for arm in workers:
            row[arm] = [persistent[arm][i] for i in indices]
            row["fresh_" + arm] = [observe(arm, [case])[0] for _ in range(2)]
        repeat.append(row)
        for index in indices:
            item = {"case_id": case["case_sha256"]}
            for arm in workers:
                raw = persistent[arm][index]["diagnostics"]["call_lifecycle"]
                item[arm] = {**raw, "available": all(raw[p]["available"] for p in ("before", "after"))}
            lifecycle.append(item)
    identities = {}
    for arm in workers:
        pids = {value["diagnostics"]["call_lifecycle"]["before"]["scope_pid"] for value in persistent[arm]}
        assert len(pids) == 1
        identities[arm] = {"mode": "persistent", "target_library_in_process": True,
                           "worker_id": str(next(iter(pids))), "calls": len(sequence)}
    save("repeat-facts.json", {"format": "judge-generic-call-evidence-v1", "applicable": True,
        "lifecycle_policy": {"kind": "go-goroutines-v1", "native_extensions": False,
                             "candidate_module_prefixes": ["github.com/dimitri/sqlfmt"]},
        "workers": identities, "cases": repeat, "lifecycle": lifecycle})
    save("build-receipt.json", {"arms": builds, "compiler": subprocess.check_output([go, "version"], text=True).strip(),
                                "stream_cases": len(stream), "alias_cases": len(aliases),
                                "persistent_calls_per_arm": len(sequence),
                                "comparison": "central exact contract; OS errors type/errno/operation, not message"})

if __name__ == "__main__":
    main()
