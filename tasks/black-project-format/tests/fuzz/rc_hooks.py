"""Task-local RC observations only; shared judge owns all gate classifications."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from . import adapter
from .smoke import run_case

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "tools/runner"))
from runner import Context, Step
from docker_executor import DockerExecutor
from judge.verifier import build_inventory

PRECONDITION = adapter.precondition
COMPARE = adapter.compare


def CLI_CASES(context):
    """Public CLI requests only; the shared runner executes and classifies them."""
    cases = []
    for payload_class, text in (
        ("small", "value=  1\n"),
        ("large", "".join(f"value_{index:05d}=  {index}\n" for index in range(10000))),
    ):
        for operation in ("stdin", "inplace"):
            seed = Path(context["out"]) / "cli-inputs" / (operation + "-" + payload_class)
            seed.mkdir(parents=True, exist_ok=False)
            for name in ("home", "cache", "project"):
                (seed / name).mkdir()
            source = seed / ("input.py" if operation == "stdin" else "project/example.py")
            source.write_text(text, encoding="utf-8", newline="")
            seed.chmod(0o755)
            for path in seed.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            case = _base_case("black.cli")
            case["input"]["text"] = text
            case["options"]["cli_mode"] = "stdin" if operation == "stdin" else "write"
            case["generator_id"] = "black-generic-rc-cli-" + operation + "-" + payload_class
            cases.append({
                "case": case, "surface": _surface(case), "inputs": seed,
                "payload_class": payload_class,
                "step": {
                    "argv": ["python3", "-m", "black", "--no-cache", "--line-length", "88",
                             "-" if operation == "stdin" else "project/example.py"],
                    "cwd": ".", "timeout": 60 if payload_class == "large" else 20,
                    "stdin_file": "input.py" if operation == "stdin" else None,
                    "env": {
                        "PYTHONPATH": "{build}/runtime", "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONHASHSEED": "0", "BLACK_NUM_WORKERS": "8",
                        "HOME": "{work}/home", "XDG_CACHE_HOME": "{work}/cache",
                        "PIP_NO_INDEX": "1",
                    },
                },
                "watch_inputs": ["input.py"] if operation == "stdin" else ["project"],
                "watch_outputs": ["."],
                "output_dirs": [] if operation == "stdin" else ["project"],
                "required_outputs": [] if operation == "stdin" else ["project/example.py"],
                "faults": (["healthy", "dev_full", "closed_reader", "closed_stdout"]
                           if operation == "stdin" else
                           ["healthy", "dev_full", "closed_reader", "closed_stdout", "readonly_dir"]),
            })
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases}


CLI_PRECONDITION = adapter.precondition


def HEADER_CONSUMERS(context):
    """Publish independently reviewed pinned scope, not a native-code waiver."""
    path = HERE / "header-scope.json"
    proof = json.loads(path.read_text())
    if (proof["header_paths"] or proof["c_cpp_source_paths"]
            or proof["independent_review"]["reviewer_role"] !=
            "Independent automated public-source header-scope audit"
            or proof["archive_sha256"] !=
            proof["independent_review"]["independently_verified_archive_sha256"]
            or proof["files_scanned"] != proof["independent_review"]["independently_verified_files"]
            or proof["file_inventory_sha256"] !=
            proof["independent_review"]["independently_verified_file_inventory_sha256"]):
        raise ValueError("Public header applicability proof needs renewed review")
    return {
        "format": "judge-generic-header-request-v1", "applicable": False,
        "reason": proof["independent_review"]["decision"],
        "applicability_evidence": {
            "scope": proof, "proof_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "limits": "No public C/C++ consumer API; native PyInit/import, lifecycle and instrumentation remain required",
        },
    }


def _monitor():
    path = Path(os.environ.get("BLACK_FUZZ_MONITOR_FILE",
                               str(REPO / "tools/fuzz_api_faults/lifecycle.py"))).resolve()
    if not path.is_file():
        raise ValueError("Shared per-call lifecycle monitor unavailable")
    return path


def _surface(case):
    manifest = json.loads((HERE / "surface.json").read_text())
    return next(row for row in manifest["surfaces"] if row["id"] == case["surface_id"])


def _base_case(surface="black.format_str"):
    return {
        "task": adapter.TASK, "surface_id": surface, "contract_id": adapter.CONTRACT,
        "precondition_id": adapter.PRECONDITION, "generator_id": "black-generic-rc-public-v1",
        "input": {"text": "x=1\n", "filename": "example.py"},
        "options": {"line_length": 88, "target_versions": ["PY310"],
                    "string_normalization": True, "fast": False, "ranges": []},
        "sequence": [{"op": "call"}], "faults": [],
    }


def _observe(context, case, arm, monitor):
    observation, transport = run_case(
        case, Path(context[arm]), context["image"], context["cpuset"], "python3", monitor)
    if observation["identity"]["build_sha256"] != context["builds"][arm]:
        raise ValueError("Generic context build identity differs from actual imported arm")
    return observation, transport


def _case_row(context, case, monitor):
    result = {"case": case, "surface": _surface(case), "transport": {}}
    for arm in ("baseline", "candidate"):
        values = [_observe(context, case, arm, monitor) for _ in range(2)]
        result[arm] = [value[0] for value in values]
        result["transport"][arm] = [value[1] for value in values]
    return result


def stream_faults(context):
    """Initial library file-I/O faults; not a claim of every partial-I/O class."""
    monitor = _monitor()
    cases = []
    for fault in ("readonly-input-file", "missing-input-file"):
        case = _base_case("black.format_file_in_place")
        case["options"]["write_back"] = "YES"
        case["faults"] = [{"kind": fault}]
        cases.append(_case_row(context, case, monitor))
    return {
        "format": "judge-generic-call-evidence-v1", "applicable": True, "cases": cases,
        "scope": "Public file-format API permission/missing-input errors under UID10000",
        "limitations": "Required CLI ENOSPC/EPIPE/closed-stdout/readonly-directory and write/flush coverage is a pending generic-RC integration dependency; partial/EINTR applies only to supported public stream contracts",
        "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest(),
    }


def aliasing(context):
    monitor = _monitor()
    case = _base_case()
    case["sequence"] = [{"op": "call"}, {"op": "alternate_mode"}, {"op": "clear_targets"},
                        {"op": "alternate_mode"}, {"op": "call"}]
    return {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": [_case_row(context, case, monitor)],
        "scope": "Caller-owned Mode target set mutation plus independent alternate Mode",
        "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest(),
    }


def _persistent(context, cases, arm, monitor):
    with tempfile.TemporaryDirectory(prefix="black-rc-persistent-") as directory:
        scratch = Path(directory)
        (scratch / "cases.json").write_text(json.dumps(cases, ensure_ascii=False))
        build = Path(context[arm]).resolve()
        ctx = Context(scratch, build, scratch, scratch, HERE, docker=True)
        executor = DockerExecutor(
            context["image"], context["cpuset"], memory="16g",
            extra_docker_flags=[
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "-v", f"{HERE}:/verifier-fuzz:ro",
                "-v", f"{monitor}:/verifier-monitor.py:ro"],
            tmpfs="/tmp:rw,nosuid,nodev,size=512m")
        worker_id = uuid.uuid4().hex
        argv = ["python3", "/verifier-fuzz/persistent_worker.py", "--cases", "/scratch/cases.json",
                "--runtime", "/app/build/runtime", "--build-sha256", build_inventory(build),
                "--monitor", "/verifier-monitor.py"]
        result = executor.run([Step(argv, timeout=60)], ctx, {
            "OPTIMBENCH_FUZZ_WORKER_ID": worker_id, "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0", "HOME": "/scratch", "TZ": "UTC", "LC_ALL": "C.UTF-8"})[0]
        if result.exit_code or result.leftovers:
            raise ValueError("Persistent native worker transport failed: " +
                             result.stderr.decode(errors="replace")[-2000:])
        payload = json.loads(result.stdout)
        if payload.get("format") != "black-persistent-worker-observations-v1":
            raise ValueError("Malformed persistent native worker result")
        observations = payload["observations"]
        if len(observations) != len(cases):
            raise ValueError("Persistent worker lost cases")
        for case, observation in zip(cases, observations):
            diagnostics = observation["diagnostics"]
            if (diagnostics["worker_instance_id"] != worker_id or not diagnostics["compiled"]
                    or not diagnostics["library_origin"].startswith("/app/build/runtime/")
                    or not diagnostics["library_origin"].endswith(".so")
                    or observation["identity"]["case_sha256"] != adapter.case_sha256(case)
                    or observation["identity"]["build_sha256"] != context["builds"][arm]):
                raise ValueError("Persistent worker identity/native import mismatch")
        return observations, {
            "mode": "persistent", "target_library_in_process": True,
            "worker_id": worker_id, "calls": payload["calls"],
            "monitor_sha256": payload["monitor_sha256"],
        }


def _snapshot_view(raw):
    """Count fields are derived from the shared monitor's exact identity lists."""
    return {
        "fds": raw.get("counts", {}).get("fds"),
        "threads": raw.get("counts", {}).get("threads"),
        "children": raw.get("counts", {}).get("children"),
        "thread_identities": raw.get("thread_identities"),
        "fd_identities": raw.get("fds"), "child_identities": raw.get("children"),
        "raw_snapshot": raw,
    }


def repeat_in_process(context):
    monitor = _monitor()
    a = _base_case()
    b = copy.deepcopy(a)
    b["input"]["text"] = "text='λ🙂'\n"
    b["options"].update(line_length=20, string_normalization=False, preview=True)
    ordered = [a, b, a, b]
    persisted, workers = {}, {}
    for arm in ("baseline", "candidate"):
        persisted[arm], workers[arm] = _persistent(context, ordered, arm, monitor)
    rows, lifecycle = [], []
    for indexes, case in (((0, 2), a), ((1, 3), b)):
        row = {"case": case, "surface": _surface(case)}
        for arm in ("baseline", "candidate"):
            row[arm] = [persisted[arm][index] for index in indexes]
            row["fresh_" + arm] = [_observe(context, case, arm, monitor)[0] for _ in range(2)]
        rows.append(row)
        for repetition in range(2):
            calls = {arm: row[arm][repetition]["diagnostics"]["calls"]
                     for arm in ("baseline", "candidate")}
            if len(calls["baseline"]) != len(calls["candidate"]):
                raise ValueError("Per-call lifecycle journal lost calls")
            for index in range(len(calls["baseline"])):
                item = {"case_id": adapter.case_sha256(case) + ":" + str(index)}
                for arm in ("baseline", "candidate"):
                    raw = calls[arm][index]
                    item[arm] = {
                        "available": raw["before"].get("available") is True and raw["after"].get("available") is True,
                        "before": _snapshot_view(raw["before"]), "after": _snapshot_view(raw["after"]),
                    }
                lifecycle.append(item)
    return {
        "format": "judge-generic-call-evidence-v1", "applicable": True,
        "cases": rows, "workers": workers, "lifecycle": lifecycle,
        "scope": "Actual same-process native import, A/B/A/B public calls, compared with fresh-process oracles",
        "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest(),
    }


GATES = {
    "stream_faults": stream_faults,
    "aliasing": aliasing,
    "repeat_in_process": repeat_in_process,
}
