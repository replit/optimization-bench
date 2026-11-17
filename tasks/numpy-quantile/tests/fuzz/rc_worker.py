"""In-process NumPy quantile worker for RC facts (verifier-owned, runs in the task image).

Modes:
  persistent: read one JSON case per stdin line, run it in THIS process
              (the target library stays imported), write one JSON line back.
  fresh:      run exactly one case from argv[3] and exit.

Every reply carries an optimbench-fuzz-observation-v1 record plus per-call
lifecycle snapshots (fds, live thread identities, children). Serialization uses
the standard library on raw buffer bytes; it never calls the function under
test to describe its own result.
"""
import errno as errno_mod
import json
import math
import os
import struct
import sys
import warnings

site, mode, build_sha = sys.argv[1], sys.argv[2], sys.argv[4] if len(sys.argv) > 4 else sys.argv[3]
sys.path.insert(0, site)
import numpy as np  # noqa: E402

assert np.__file__.startswith(site), np.__file__
PID = os.getpid()
PF_EXITING = 0x00000004  # Linux task flag: in do_exit, no longer a live worker


def _stat_fields(path):
    raw = open(path, "rb").read().decode(errors="replace")
    # comm may contain spaces/parens; fields after the LAST ')' are positional.
    tail = raw[raw.rindex(")") + 2:].split()
    return tail  # tail[0] is field 3 (state); field 22 (starttime) is tail[19]


def snapshot():
    threads = []
    for tid in sorted(int(t) for t in os.listdir("/proc/self/task")):
        try:
            tail = _stat_fields(f"/proc/self/task/{tid}/stat")
        except (FileNotFoundError, ProcessLookupError):
            continue  # exited between listing and reading; not live
        if tail[0] in ("Z", "X", "x"):
            continue
        flags = int(tail[6])  # field 9; same kernel fact the shared samplers read
        if flags & PF_EXITING:
            continue  # already in do_exit (e.g. just joined); not a live worker
        threads.append({"tid": tid, "start_ticks": int(tail[19]), "state": tail[0],
                        "flags": flags, "scope_pid": PID, "tgid": PID})
    children = 0
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) != PID:
            try:
                if int(_stat_fields(f"/proc/{entry}/stat")[1]) == PID:
                    children += 1
            except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
                continue
    fds = len(os.listdir("/proc/self/fd"))
    return {"fds": fds, "threads": len(threads), "children": children,
            "thread_identities": threads}


def build_array(spec):
    rng = np.random.Generator(np.random.PCG64(spec["seed"]))
    shape = tuple(spec["shape"])
    a = rng.standard_normal(shape) if shape else np.asarray(rng.standard_normal())
    kind = spec.get("kind", "normal")
    if kind == "nan_some" and a.size:
        a.reshape(-1)[:: max(1, a.size // 7)] = np.nan
    elif kind == "signed_zeros":
        a = np.where(rng.random(shape) < 0.5, 0.0, -0.0)
    elif kind == "ties":
        a = rng.integers(0, 3, shape).astype(float)
    elif kind == "inf_mix":
        a = rng.choice(np.array([np.inf, -np.inf, 0.0, 1.0]), shape)
    a = a.astype(spec["dtype"])
    layout = spec.get("layout", "C")
    if layout == "F":
        a = np.asfortranarray(a)
    elif layout == "strided" and a.ndim:
        a = np.repeat(a, 2, axis=0)[::2]
    elif layout == "readonly":
        a = a.copy(); a.setflags(write=False)
    return a


def encode(value):
    arr = np.asarray(value)
    flat = arr.reshape(-1)
    if arr.dtype.kind == "f" and arr.dtype.itemsize in (4, 8):
        little = np.ascontiguousarray(flat, dtype=arr.dtype.newbyteorder("<"))
        code = "d" if arr.dtype.itemsize == 8 else "f"
        vals = struct.unpack("<" + code * flat.size, bytes(memoryview(little))) if flat.size else ()
        return {"type": type(value).__name__, "dtype": arr.dtype.str.replace(">", "<"),
                "shape": list(arr.shape), "encoding": "float-hex",
                "values": [float(v).hex() for v in vals]}
    return {"type": type(value).__name__, "dtype": arr.dtype.str, "shape": list(arr.shape),
            "encoding": "repr", "values": [repr(x) for x in flat.tolist()]}


def call(case, a):
    func = getattr(np, case["surface_id"].split(".")[1])
    opts = case["options"]
    kwargs = {k: (tuple(v) if k == "axis" and isinstance(v, list) else v)
              for k, v in opts.items() if k != "q" and v is not None}
    args = (a,) if "q" not in opts else (a, opts["q"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = func(*args, **kwargs)
            outcome = {"kind": "ok", "value": encode(result)}
        except Exception as error:  # expected library errors are valid outcomes
            result = None
            outcome = {"kind": "error", "class": type(error).__name__, "message": str(error)}
            if isinstance(error, OSError):
                outcome["os_errno"] = error.errno
    outcome["warnings"] = [[type(w.message).__name__, str(w.message)] for w in caught]
    return result, outcome


def run(case):
    before = snapshot()
    a = build_array(case["input"])
    data_before = a.tobytes()
    effects = {}
    if case["sequence"] == "alias":
        # Public result independence (Validation2 control). Retain live results
        # and re-check them after later calls with the SAME result shape but
        # different data, so a reused output buffer is visible.
        other = (a[::-1] + 1.0) if a.dtype.kind == "f" else a[::-1]
        other = np.array(other, dtype=a.dtype)
        r1, o1 = call(case, a)
        frozen1 = encode(r1) if r1 is not None else None
        r2, o2 = call(case, other)
        effects["first_result_unchanged_after_second_call"] = (
            r1 is None or encode(r1) == frozen1)
        frozen2 = encode(r2) if r2 is not None else None
        writable = isinstance(r2, np.ndarray) and r2.size > 0 and r2.flags.writeable
        effects["second_result_writable"] = bool(writable)
        if writable:
            r2.reshape(-1)[0] = 123.0
        r3, o3 = call(case, other)
        effects["third_result_unaffected_by_second_mutation"] = (o3 == o2)
        frozen3 = encode(r3) if r3 is not None else None
        call(case, a)
        effects["third_result_unchanged_after_later_call"] = (
            r3 is None or encode(r3) == frozen3)
        outcome = {**o1, "warnings": o1["warnings"] + o2["warnings"] + o3["warnings"]}
        del frozen2
    elif case["sequence"] in ("protocol_fault", "protocol_healthy"):
        # Documented public user-callback protocols on array_like inputs:
        # __array__ (array coercion) and __array_function__ (dispatch). A
        # faulting callback raises OSError(EPIPE/ENOSPC); record invocation
        # counts, dispatched public function name, continuation and exact
        # propagated exception (class, errno, message).
        proto = case["input"]["protocol"]
        code = getattr(errno_mod, case["input"].get("errno", "EPIPE"))
        fault = case["sequence"] == "protocol_fault"
        log = {"array_calls": 0, "array_function_calls": 0, "dispatched": [], "after_raise": 0}

        class ArrayProtocolInput:
            def __array__(self, dtype=None, copy=None):
                log["array_calls"] += 1
                if log.get("raised"):
                    log["after_raise"] += 1
                if fault and proto == "array":
                    log["raised"] = True
                    raise OSError(code, os.strerror(code))
                return a if dtype is None else a.astype(dtype)

            if proto == "array_function":
                def __array_function__(self, func, types, args, kwargs):
                    log["array_function_calls"] += 1
                    log["dispatched"].append(func.__name__)
                    if log.get("raised"):
                        log["after_raise"] += 1
                    log["raised"] = True
                    raise OSError(code, os.strerror(code))

        _, outcome = call(case, ArrayProtocolInput())
        if outcome["kind"] == "error":
            outcome["errno"] = case["input"].get("errno") if fault else None
        effects.update({k: v for k, v in log.items() if k != "raised"})
    else:
        _, outcome = call(case, a)
    effects["input_unchanged"] = a.tobytes() == data_before
    after = snapshot()
    warnings_list = outcome.pop("warnings")
    obs = {
        "format": "optimbench-fuzz-observation-v1",
        "identity": {"task": case["task"], "surface_id": case["surface_id"],
                     "case_sha256": case["case_sha256"], "build_sha256": build_sha},
        "termination": {"kind": "return", "exit_code": 0},
        "outcome": outcome,
        "effects": {**effects, "warnings": warnings_list},
        "diagnostics": {"pid": PID, "affinity": sorted(os.sched_getaffinity(0)),
                        "execution_affinity": sorted(os.sched_getaffinity(0))},
    }
    return {"observation": obs, "lifecycle": {"available": True, "before": before, "after": after}}


if mode == "fresh":
    print(json.dumps(run(json.loads(open(sys.argv[3]).read()))))
else:
    print(json.dumps({"ready": True, "pid": PID, "affinity": sorted(os.sched_getaffinity(0)),
                      "numpy": np.__file__}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        print(json.dumps(run(json.loads(line))), flush=True)
