"""Native in-process public API replay within the pinned task worker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import adapter


def _observations(stdout):
    values = [json.loads(line) for line in stdout.splitlines()]
    for value in values:
        diagnostics = value["diagnostics"]
        native_affinity = diagnostics.get("execution_affinity")
        diagnostics["native_execution_affinity"] = native_affinity
        cpus = native_affinity.get("cpus") if isinstance(native_affinity, dict) else None
        diagnostics["execution_affinity"] = (
            cpus
            if isinstance(native_affinity, dict) and native_affinity.get("available") is True
            and isinstance(cpus, list) and cpus
            and all(type(cpu) is int and cpu >= 0 for cpu in cpus)
            else None)
    return values


def main():
    parser = argparse.ArgumentParser()
    for name in ("baseline", "candidate", "baseline-sha", "candidate-sha", "out"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    workers, receipts = {}, {}
    for arm in ("baseline", "candidate"):
        source = Path(getattr(args, arm))/"project"
        env = {**os.environ, "PATH": "/usr/local/go/bin:"+os.environ.get("PATH", ""),
               "GOCACHE":"/tmp/goldmark-rc-cache", "GOMODCACHE":"/tmp/goldmark-rc-modcache",
               "OPTIMBENCH_SOURCE": str(source),
               "OPTIMBENCH_FUZZ_OUT": str(out/arm)}
        result = subprocess.run(["sh", str(Path(__file__).parent/"build-worker.sh")],
                                env=env, capture_output=True, timeout=180)
        (out/f"{arm}-build.stdout").write_bytes(result.stdout)
        (out/f"{arm}-build.stderr").write_bytes(result.stderr)
        result.check_returncode()
        workers[arm] = out/arm/"worker"
        receipts[arm] = {"admitted_build_sha256": getattr(args, arm+"_sha"),
                         "probe_binary_sha256": hashlib.sha256(workers[arm].read_bytes()).hexdigest()}
    cases = []
    motif = "# Heading\n\nA @public [link][late].\n\n|a|b|\n|-|-|\n|x|y|\n\n"
    for mode in ("gfm", "commonmark", "footnotes", "custom", "context"):
        for size in (512, 131073):
            for fault in ([], [{"kind":"fail_after","limit":17}], [{"kind":"eintr_after","limit":0}]):
                source = (motif*(size//len(motif)+1))[:size]+"\n\n[late]: /target\n"
                case = {"task": adapter.TASK, "surface_id": adapter.SURFACE,
                        "contract_id":"goldmark.public.parse-render-exact",
                        "precondition_id":"goldmark.valid-markdown-and-constructed-options",
                        "input":{"markdown_utf8":source},"options":{"mode":mode},
                        "sequence":["init","parse","render","reuse","parse","render","finish"],
                        "faults":fault}
                case["case_sha256"] = adapter.digest(case)
                assert adapter.precondition(case, {})["valid"]
                cases.append(case)
    surface = {"id":adapter.SURFACE,"visibility":"public",
               "contract_id":"goldmark.public.parse-render-exact",
               "precondition_id":"goldmark.valid-markdown-and-constructed-options",
               "oracle":{"required_error_fields":{"class":"nonempty_string"}}}

    def observe(arm, sequence):
        env = {**os.environ, "OPTIMBENCH_ADMITTED_BUILD_SHA256":getattr(args, arm+"_sha")}
        result = subprocess.run([str(workers[arm])], env=env,
                    input="".join(json.dumps(case)+"\n" for case in sequence).encode(),
                    capture_output=True, timeout=180)
        result.check_returncode()
        values = _observations(result.stdout)
        assert len(values) == len(sequence)
        return values

    rows = []
    for case in cases:
        row = {"case":case,"surface":surface}
        for arm in workers:
            row[arm] = [observe(arm, [case])[0] for _ in range(2)]
        rows.append(row)
    (out/"stream-facts.json").write_text(json.dumps({
        "format":"judge-generic-call-evidence-v1","applicable":True,"cases":rows}))
    # A/B/A, twice, in ONE native process per arm. Each operation constructs/
    # reuses only documented public objects; there is no per-call subprocess.
    selected = [case for case in cases if not case["faults"]]
    sequence = selected+list(reversed(selected))+selected
    persistent = {arm: observe(arm, sequence) for arm in workers}
    repeat_rows, lifecycle = [], []
    for case in selected:
        row = {"case":case,"surface":surface}
        indices = [i for i,item in enumerate(sequence) if item["case_sha256"] == case["case_sha256"]]
        for arm in workers:
            row[arm] = [persistent[arm][i] for i in indices]
            row["fresh_"+arm] = [observe(arm,[case])[0] for _ in range(2)]
        repeat_rows.append(row)
        for index in indices:
            record = {"case_id":case["case_sha256"]}
            for arm in workers:
                raw = persistent[arm][index]["diagnostics"]["call_lifecycle"]
                record[arm] = {"available":all(raw[p]["available"] for p in ("before","after")), **raw}
            lifecycle.append(record)
    worker_receipts = {}
    for arm in workers:
        pids = {obs["diagnostics"]["call_lifecycle"]["before"]["scope_pid"] for obs in persistent[arm]}
        assert len(pids) == 1
        worker_receipts[arm] = {"mode":"persistent","target_library_in_process":True,
            "worker_id":str(next(iter(pids))),"calls":len(sequence)}
    (out/"repeat-facts.json").write_text(json.dumps({
        "format":"judge-generic-call-evidence-v1","applicable":True,
        "lifecycle_policy":{"kind":"go-goroutines-v1","native_extensions":False,
                            "candidate_module_prefixes":["github.com/yuin/goldmark/v2"]},
        "cases":repeat_rows,"workers":worker_receipts,"lifecycle":lifecycle}))
    alias_surface={"id":adapter.ALIAS_SURFACE,"visibility":"public",
                   "contract_id":"goldmark.public.ast-instance-isolation",
                   "precondition_id":"goldmark.public.ast-construction-and-mutation",
                   "oracle":{"required_error_fields":{"class":"nonempty_string"}}}
    alias_rows=[]
    for value in ("first", "second", "unicode-λ"):
        case={"task":adapter.TASK,"surface_id":adapter.ALIAS_SURFACE,
              "contract_id":alias_surface["contract_id"],
              "precondition_id":alias_surface["precondition_id"],
              "input":{"metadata_value":value},"options":{"mode":"ast_metadata"},
              "sequence":["factory","getter","mutate","interleave","fresh","finish"],"faults":[]}
        case["case_sha256"]=adapter.digest(case)
        assert adapter.precondition(case,alias_surface)["valid"]
        row={"case":case,"surface":alias_surface}
        for arm in workers:
            row[arm]=[observe(arm,[case])[0] for _ in range(2)]
        alias_rows.append(row)
    (out/"alias-facts.json").write_text(json.dumps({
        "format":"judge-generic-call-evidence-v1","applicable":True,"cases":alias_rows}))
    (out/"build-receipt.json").write_text(json.dumps({
        "arms":receipts,"compiler":subprocess.check_output(["/usr/local/go/bin/go","version"]).decode(),
        "cases":len(cases),"persistent_calls_per_arm":len(sequence)},indent=2)+"\n")


if __name__ == "__main__":
    main()
