"""Run one adapter case against one NumPy site directory; print an observation.

Usage: python3 driver.py <site-dir> <case.json> <build_sha256>

The report is serialized with the standard library from raw buffer bytes; it
never calls the quantile functions under test to describe its own output.
"""
import hashlib
import json
import math
import os
import random
import struct
import sys
import warnings

site, case_path, build_sha = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, site)
import numpy as np  # noqa: E402

assert np.__file__.startswith(site), np.__file__
case = json.load(open(case_path))


def threads():
    return len(os.listdir("/proc/self/task"))


def values(kind, n, dtype, rng):
    r = random.Random(rng)
    base = [r.gauss(0, 1) for _ in range(n)]
    if kind == "nan_some" and n:
        for i in r.sample(range(n), max(1, n // 50)):
            base[i] = math.nan
    elif kind == "all_nan":
        base = [math.nan] * n
    elif kind == "signed_zeros":
        base = [r.choice([0.0, -0.0]) for _ in range(n)]
    elif kind == "inf_mix":
        base = [r.choice([math.inf, -math.inf, 0.0, 1.0]) for _ in range(n)]
    elif kind == "subnormal":
        base = [r.random() * 5e-324 * 50 for _ in range(n)]
    elif kind == "ties":
        base = [float(r.randrange(3)) for _ in range(n)]
    elif kind == "dup_heavy":
        pool = [r.gauss(0, 1) for _ in range(max(1, n // 1000))]
        base = [pool[i % len(pool)] for i in range(n)]
    elif kind == "sorted":
        base.sort()
    elif kind == "reversed":
        base.sort(reverse=True)
    elif kind == "huge":
        base = [x * 4e307 for x in base]
    elif kind == "tiny":
        base = [x * 2.2e-308 for x in base]
    elif kind == "mixed_mag":
        base = [x * 10.0 ** r.randrange(-30, 30) for x in base]
    elif kind == "constant":
        base = [3.25] * n
    with np.errstate(all="ignore"):
        if dtype == "object":
            return np.array(base, dtype=object)
        if dtype == "bool":
            return np.array([x > 0 for x in base])
        if np.dtype(dtype).kind in "iu":
            info = np.iinfo(dtype)
            return np.array([int(max(info.min, min(info.max, x * 100))) if math.isfinite(x) else 0
                             for x in base], dtype=dtype)
        return np.array(base, dtype=dtype)


def build_input(spec):
    shape = spec["shape"]
    n = 1
    for s in shape:
        n *= s
    flat = values(spec["kind"], n, spec["dtype"], spec["seed"])
    a = flat.reshape(shape) if shape else flat.reshape(()) if n == 1 else np.asarray(flat[0] if n else 0.0)
    layout = spec["layout"]
    if layout == "F":
        a = np.asfortranarray(a)
    elif layout == "strided" and a.ndim:
        big = np.repeat(a, 2, axis=0)
        a = big[::2]
    elif layout == "negative" and a.ndim:
        a = a[::-1].copy()[::-1]
    elif layout == "transposed" and a.ndim > 1:
        a = np.ascontiguousarray(a.T).T
    elif layout == "unaligned" and a.dtype.kind in "fiu" and a.size:
        raw = np.zeros(a.nbytes + 1, dtype=np.uint8)
        u = np.frombuffer(raw.data, dtype=a.dtype, count=a.size, offset=1).reshape(a.shape)
        u[...] = a
        a = u
    elif layout == "byteswapped" and a.dtype.kind in "fiuc" and a.dtype.itemsize > 1:
        a = a.astype(a.dtype.newbyteorder())
    elif layout == "readonly":
        a = a.copy(); a.setflags(write=False)
    elif layout == "broadcast" and a.ndim:
        a = np.broadcast_to(a, (2,) + a.shape)
    return a


def q_value(opts, func):
    r = random.Random(opts["q_seed"])
    scale = 100.0 if "percentile" in func else 1.0
    form = opts["q_form"]
    if form == "scalar":
        return r.random() * scale
    if form == "list":
        return [r.random() * scale for _ in range(r.randrange(1, 6))]
    if form == "array_f4":
        return np.array([r.random() * scale for _ in range(3)], dtype=np.float32)
    if form == "edge":
        return [0.0, 0.5 * scale, 1.0 * scale]
    return r.choice([-0.1 * scale, 1.5 * scale, [[0.5 * scale]], math.nan, "0.5"])


def encode(value):
    arr = np.asarray(value)
    shape = list(arr.shape)
    flat = arr.reshape(-1) if arr.ndim else arr.reshape(1)
    if arr.dtype.kind == "f" and arr.dtype.itemsize in (4, 8):
        fmt = "<" + ("d" if arr.dtype.itemsize == 8 else "f") * flat.size
        raw = bytes(memoryview(np.ascontiguousarray(flat.astype(arr.dtype.newbyteorder("<")))))
        vals = list(struct.unpack(fmt, raw)) if flat.size else []
        return {"dtype": arr.dtype.str.replace(">", "<"), "shape": shape, "encoding": "float",
                "values": [v if math.isfinite(v) else repr(v) for v in vals]}
    return {"dtype": arr.dtype.str, "shape": shape, "encoding": "repr",
            "values": [repr(x) for x in flat.tolist()]}


def snapshot(a):
    base = a
    while isinstance(getattr(base, "base", None), np.ndarray):
        base = base.base
    # tobytes() works for every dtype/byte order (memoryview rejects non-native 'g').
    return hashlib.sha256(base.tobytes() if base.dtype != object
                          else repr(base.tolist()).encode()).hexdigest()


func_name = case["surface_id"].split(".")[-1]
func = getattr(np, func_name)
opts = case["options"]
termination = {"kind": "return", "exit_code": 0}
calls, effects, retained = [], {}, []
try:
    a = build_input(case["input"])
    before = snapshot(a)
    threads_before = threads()
    kwargs = {"keepdims": opts["keepdims"]}
    if opts["axis"] is not None:
        kwargs["axis"] = tuple(opts["axis"]) if isinstance(opts["axis"], list) else opts["axis"]
    if opts.get("method") is not None:
        kwargs["method"] = opts["method"]
    if opts.get("overwrite_input"):
        a = a.copy() if a.flags.writeable else a
        kwargs["overwrite_input"] = True
    args = (a,) if func_name in ("median", "nanmedian") else (a, q_value(opts, func_name))
    reps = {"single": 1, "aba_retain_outputs": 3, "interleaved_instances": 3, "repeat_same_input": 2}[case["sequence"]]
    other = np.array([7.0, -1.0, 3.0])
    for i in range(reps):
        if case["sequence"] == "interleaved_instances" and i == 1:
            call_args = (other,) + args[1:]
        else:
            call_args = args
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                r = func(*call_args, **kwargs)
                rec = {"status": "ok", "value": encode(r)}
                retained.append((r, json.dumps(rec["value"], sort_keys=True)))
            except Exception as e:
                rec = {"status": "error", "type": type(e).__name__, "message": str(e)}
        rec["warnings"] = [[type(w.message).__name__, str(w.message)] for w in caught]
        calls.append(rec)
    effects["input_unchanged"] = True if kwargs.get("overwrite_input") else snapshot(a) == before
    # Retained earlier outputs must not change after later calls (aliasing).
    effects["earlier_outputs_unchanged"] = all(
        json.dumps(encode(r), sort_keys=True) == s for r, s in retained)
    effects["threads_after_equal_before"] = threads() == threads_before
except BaseException as e:  # harness/setup failures are visible, not silent passes
    termination = {"kind": "exception", "type": type(e).__name__, "message": str(e)[:300]}
obs = {
    "format": "optimbench-fuzz-observation-v1",
    "identity": {"task": case["task"], "surface_id": case["surface_id"],
                 "case_sha256": case["case_sha256"], "build_sha256": build_sha},
    "termination": termination,
    "outcome": {"kind": "ok", "calls": calls},
    "effects": effects,
    "diagnostics": {"numpy_file": np.__file__},
}
print(json.dumps(obs, sort_keys=True))
