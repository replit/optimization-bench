"""Protected native workers; raw observations, never a task verdict."""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess

TASK = "xz-lzma2-compress"
SURFACE = {
    "id": "liblzma-stream-v1", "visibility": "public",
    "contract_id": "liblzma-stream-state-and-owned-buffers-v1",
    "precondition_id": "bounded-libxz-native-cases-v1",
    "oracle": {"required_error_fields": {"code": "integer", "category": "nonempty_string"}},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def corpus():
    return [{"task": TASK, "surface_id": SURFACE["id"], "contract_id": SURFACE["contract_id"],
             "precondition_id": SURFACE["precondition_id"], "operation": operation,
             "input_chunk": chunk, "output_chunk": output}
            for operation, chunk, output in (
                (0, 1, 1), (0, 7, 17), (1, 1, 1), (1, 7, 17), (2, 7, 17),
                (3, 7, 17), (4, 7, 17), (5, 7, 17), (6, 1, 17), (6, 7, 1),
                (7, 7, 17), (8, 7, 17))]


def precondition(case, surface):
    valid = (surface == SURFACE and case in corpus()
             and all(type(case.get(key)) is int for key in
                     ("operation", "input_chunk", "output_chunk")))
    return {"valid": valid, "reason": "frozen, bounded public stream/ownership case" if valid
            else "unknown public case or surface"}


def snapshot(pid):
    root = Path("/proc") / str(pid)
    identities = []
    for item in sorted((root / "task").iterdir(), key=lambda p: int(p.name)):
        fields = (item / "stat").read_text().rsplit(")", 1)[1].split()
        identities.append({"tid": int(item.name), "start_ticks": int(fields[19]),
                           "state": fields[0], "flags": int(fields[6]),
                           "scope_pid": pid, "tgid": pid})
    children = []
    for item in Path("/proc").iterdir():
        if item.name.isdigit():
            try:
                fields = (item / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == pid:
                    children.append(int(item.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
    return {"fds": len(list((root / "fd").iterdir())), "threads": len(identities),
            "children": len(children), "thread_identities": identities}


def execute(binary, fixtures, cases, build_sha):
    process = subprocess.Popen([str(binary), str(fixtures)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rows, lifecycle = [], []
    try:
        if json.loads(process.stdout.readline()) != {"ready": True}:
            raise ValueError("Native worker did not initialize")
        for case in cases:
            before = snapshot(process.pid)
            process.stdin.write(f"{case['operation']} {case['input_chunk']} {case['output_chunk']}\n")
            process.stdin.flush()
            raw = json.loads(process.stdout.readline())
            after = snapshot(process.pid)
            if raw["pid"] != process.pid:
                raise ValueError("Native worker identity changed")
            status = raw["effects"]["init"] or raw["effects"].get("first", {}).get("ret", 0)
            outcome = ({"kind": "ok", "value": status} if status in (0, 1) else
                       {"kind": "error", "code": status, "category": "lzma_ret"})
            observation = {
                "format": "optimbench-fuzz-observation-v1",
                "identity": {"task": TASK, "surface_id": SURFACE["id"],
                             "case_sha256": digest(case), "build_sha256": build_sha},
                "termination": {"kind": "return", "exit_code": 0},
                "outcome": outcome,
                "effects": raw["effects"],
                "diagnostics": {"pid": process.pid, "execution_affinity": raw["affinity"]},
            }
            rows.append(observation)
            lifecycle.append({"available": True, "before": before, "after": after})
        process.stdin.close()
        process.wait(timeout=30)
        stderr = process.stderr.read()
        if process.returncode or stderr or process.stdout.read():
            raise ValueError("Native worker failed: " + stderr)
        return rows, lifecycle, process.pid
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


def run(baseline, candidate, baseline_sha, candidate_sha, out):
    out.mkdir(parents=True, exist_ok=False)
    fixtures = out / "fixtures"
    fixtures.mkdir()
    plain = bytes(range(128)) + b"liblzma owned input and retained output\n" * 3
    frame = lzma.compress(plain, preset=0, check=lzma.CHECK_CRC64)
    corrupt = bytearray(frame)
    corrupt[-20] ^= 1
    for name, data in {"plain": plain, "frame": frame, "truncated": frame[:-9],
                       "corrupt": corrupt, "concatenated": frame + b"\0" * 4 + frame}.items():
        (fixtures / name).write_bytes(data)
    cases = corpus()
    facts = {"format": "judge-generic-call-evidence-v1", "applicable": True, "cases": [],
             "workers": {}, "lifecycle": [], "lifecycle_policy": {"kind": "native-os-threads"},
             "coverage": "bounded stream failures, reinitialization and shared immutable input; "
                         "arbitrary overlapping compression buffers are not a documented API guarantee"}
    histories = {}
    for arm, build, build_sha in (("baseline", baseline, baseline_sha),
                                  ("candidate", candidate, candidate_sha)):
        binary = out / (arm + "-probe")
        command = ["cc", "-O2", "-I" + str(build / "project/src/liblzma/api"),
                   str(Path(__file__).with_name("rc_probe.c")), str(build / "cmake/liblzma.a"),
                   "-lpthread", "-o", str(binary)]
        compiled = subprocess.run(command, capture_output=True)
        (out / (arm + "-compile.stdout")).write_bytes(compiled.stdout)
        (out / (arm + "-compile.stderr")).write_bytes(compiled.stderr)
        if compiled.returncode:
            raise ValueError("Native consumer compilation failed: " + arm)
        rows, lifecycle, pid = execute(binary, fixtures, cases + cases, build_sha)
        fresh = [execute(binary, fixtures, [case], build_sha)[0][0]
                 for case in cases for _ in range(2)]
        histories[arm] = (rows, lifecycle, fresh)
        facts["workers"][arm] = {"mode": "persistent", "target_library_in_process": True,
                                  "worker_id": str(pid), "calls": len(rows),
                                  "probe_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    for index, case in enumerate(cases):
        row = {"case": case, "surface": SURFACE}
        for arm in ("baseline", "candidate"):
            rows, lifecycle, fresh = histories[arm]
            row[arm] = [rows[index], rows[index + len(cases)]]
            row["fresh_" + arm] = fresh[index * 2:index * 2 + 2]
        facts["cases"].append(row)
        for replay in range(2):
            facts["lifecycle"].append({"case_id": digest(case), **{
                arm: histories[arm][1][index + replay * len(cases)]
                for arm in ("baseline", "candidate")}})
    for name in ("stream", "repeat", "alias"):
        selected = dict(facts)
        if name == "alias":
            selected["cases"] = [row for row in facts["cases"] if row["case"]["operation"] == 6]
        (out / (name + "-facts.json")).write_text(json.dumps(selected, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("baseline-sha", "candidate-sha"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    run(args.baseline, args.candidate, args.baseline_sha, args.candidate_sha, args.out)


if __name__ == "__main__":
    main()
