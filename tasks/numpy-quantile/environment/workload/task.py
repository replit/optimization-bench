"""Resident NumPy quantiles; complete .npy results, independent numeric comparison.

v1.1: performance cases are warm batches. One resident process loads and prepares every
distinct input before the timed region, computes all quantiles inside it, and serializes and
verifies results only after the second stop. Correctness cases keep one call per process.

Suite units: a case with a "workload" key is one additional performance unit, run by the
protected driver it names (workload/driver/suite.py) with the same resident boundary. Its
inputs are the case's listed files under data/<root>/, and its results are the case's listed
names. Unit comparison is exact unless SUITE_TOLERANCE names the unit.
"""
import ast
import io
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import sys
from runner import (Step, locked_cases, bundle_references, collected_bundle,
                    unbundle, canonical_json)

ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
       "OPENBLAS_NUM_THREADS": "8", "OMP_NUM_THREADS": "8"}
RESULT_ENCODING = "named-files-v1"
cases = locked_cases
references = bundle_references
# Suite units compared with a stated numeric tolerance; every other unit is exact.
SUITE_TOLERANCE = {"interp-equalize": "absolute-8eps", "loadtxt-csv": "equal-nan"}


def _suite_name(case, name):
    return "suite." + case.data["workload"] + "." + name + ".npy"


def _suite_prepare(case, ctx):
    root = Path(case.data["root"])
    for name in case.data["files"]:
        target = ctx.scratch / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.inputs / "data" / root / name, target)
    (ctx.scratch / "case.json").write_bytes(canonical_json(case.data))
    return [Step(["python3", str(ctx.workload / "task.py"), "--worker"],
                 env={"PYTHONPATH": str(ctx.build / "site")}, resident=True)]


def prepare(case, ctx):
    if "workload" in case.data:
        return _suite_prepare(case, ctx)
    corpus = json.loads((ctx.workload / "corpus.json").read_text())
    if "calls" in case.data:
        # v1.1 warm batch: every distinct parent is staged once; the worker loads
        # and prepares all inputs before the timed region.
        (ctx.scratch / "parents").mkdir()
        for name in sorted({call["parent"] for call in case.data["calls"]}):
            shutil.copyfile(ctx.inputs / "data" / corpus["parents"][name]["file"],
                            ctx.scratch / "parents" / (name + ".npy"))
    else:
        parent = corpus["parents"][case.data["parent"]]
        shutil.copyfile(ctx.inputs / "data" / parent["file"], ctx.scratch / "parent.npy")
    (ctx.scratch / "case.json").write_bytes(canonical_json(case.data))
    return [Step(["python3", str(ctx.workload / "task.py"), "--worker"],
                 env={"PYTHONPATH": str(ctx.build / "site")}, resident=True)]


def result_files(case, ctx):
    if "workload" in case.data:
        return {_suite_name(case, name): ctx.scratch / "results" / _suite_name(case, name)
                for name in case.data["outputs"]}
    if "calls" in case.data:
        return {call["id"] + ".npy": ctx.scratch / "results" / (call["id"] + ".npy")
                for call in case.data["calls"]}
    return {"result.npy": ctx.scratch / "result.npy"}


def collect(case, ctx, results):
    return collected_bundle(case, ctx, results, result_files(case, ctx))


def numeric_array(raw):
    stream = io.BytesIO(raw)
    if stream.read(6) != b"\x93NUMPY":
        raise ValueError("Not an npy array")
    version = stream.read(2)
    if version == b"\x01\x00":
        length = struct.unpack("<H", stream.read(2))[0]
    elif version in (b"\x02\x00", b"\x03\x00"):
        length = struct.unpack("<I", stream.read(4))[0]
    else:
        raise ValueError("Unsupported npy version")
    header = ast.literal_eval(stream.read(length).decode("latin1").strip())
    dtype = header["descr"]
    if dtype not in ("<f8", ">f8", "<f4", ">f4"):
        raise ValueError("Unsupported result dtype/layout")
    shape = header["shape"]
    if not isinstance(shape, tuple) or any(type(n) is not int or n < 0 for n in shape):
        raise ValueError("Invalid result shape")
    count = math.prod(shape)
    payload = stream.read()
    size = int(dtype[-1])
    if len(payload) != count * size:
        raise ValueError("Invalid result length")
    values = struct.unpack(dtype[0] + ("d" if size == 8 else "f") * count, payload)
    if header["fortran_order"] and count:
        # Shape/dtype are semantic; a different storage order is not a failure.
        strides = [math.prod(shape[:i]) for i in range(len(shape))]
        import itertools
        values = tuple(values[sum(i * stride for i, stride in zip(index, strides))]
                       for index in itertools.product(*(range(n) for n in shape)))
    return dtype, shape, values


def compare(expected, actual):
    """Same numeric rule for every named result (one per call in a warm batch)."""
    a, b = unbundle(expected), unbundle(actual)
    if not a or b.keys() != a.keys():
        return False
    if all(name.startswith("suite.") for name in a):
        return _suite_compare(a, b)
    eps = 8 * sys.float_info.epsilon
    for name in a:
        ad, ashape, av = numeric_array(a[name])
        bd, bshape, bv = numeric_array(b[name])
        if not (ad == bd and ashape == bshape and all(
                (math.isnan(x) and math.isnan(y)) or x == y or
                (math.isfinite(x) and math.isfinite(y) and abs(x - y) <= eps + eps * abs(x))
                for x, y in zip(av, bv))):
            return False
    return True


def _suite_compare(a, b):
    """Exact unless the unit has a declared tolerance (float64 results only)."""
    units = {name.split(".")[1] for name in a}
    rule = SUITE_TOLERANCE.get(units.pop()) if len(units) == 1 else None
    if rule is None:
        return all(a[name] == b[name] for name in a)
    eps = 8 * sys.float_info.epsilon
    for name in a:
        ad, ashape, av = numeric_array(a[name])
        bd, bshape, bv = numeric_array(b[name])
        if ad != "<f8" or (ad, ashape) != (bd, bshape):
            return False
        if rule == "absolute-8eps":
            same = all((math.isnan(x) and math.isnan(y)) or abs(x - y) <= eps for x, y in zip(av, bv))
        else:
            same = all(x == y or (math.isnan(x) and math.isnan(y)) for x, y in zip(av, bv))
        if not same:
            return False
    return True


def _serialize(np, result):
    serialized = io.BytesIO()
    np.save(serialized, result, allow_pickle=False)
    return serialized.getvalue()


def worker():
    if "workload" in json.loads(Path("case.json").read_text()):
        from driver.suite import worker as suite_worker
        return suite_worker()
    import numpy as np
    from numpy_helpers import prepare as prepare_array, arguments, buffer_hash
    case = json.loads(Path("case.json").read_text())
    calls = case["calls"] if "calls" in case else [case]
    parents = {}
    if "calls" in case:
        for call in calls:
            if call["parent"] not in parents:
                parents[call["parent"]] = np.load(Path("parents") / (call["parent"] + ".npy"), allow_pickle=False)
    else:
        parents[case["parent"]] = np.load("parent.npy", allow_pickle=False)
    # Untimed: load, prepare and validate every distinct input and its arguments.
    work = []
    for call in calls:
        array = prepare_array(parents[call["parent"]], call["prepare"])
        if {"shape": list(array.shape), "dtype": str(array.dtype), "strides": list(array.strides)} != call["input"]:
            raise ValueError("Input metadata mismatch: " + call.get("id", case["id"]))
        work.append((array, arguments(call), buffer_hash(array)))
    os.kill(os.getpid(), signal.SIGSTOP)
    # Timed: one warm process computes every quantile; materialized results and
    # their shape/dtype metadata belong to the operation. No serialization here.
    results = []
    for array, args, _ in work:
        result = np.asarray(np.quantile(array, **args))
        result.shape, result.dtype
        results.append(result)
    os.kill(os.getpid(), signal.SIGSTOP)
    # Untimed: input preservation, serialization and export.
    for array, _, before in work:
        if buffer_hash(array) != before:
            raise ValueError("Input mutated")
    if "calls" in case:
        Path("results").mkdir()
        for call, result in zip(calls, results):
            (Path("results") / (call["id"] + ".npy")).write_bytes(_serialize(np, result))
    else:
        Path("result.npy").write_bytes(_serialize(np, results[0]))


if __name__ == "__main__":
    worker()
