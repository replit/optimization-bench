"""numpy-quantile fuzz adapter (contract v1 hooks). Draft.

Hooks: generate(rng, budget), precondition(case, surface), shrink(case),
compare(baseline, candidate, case). Cases are JSON-serializable; the driver
(`driver.py`) runs one case per fresh process against a given site directory
and emits an optimbench-fuzz-observation-v1 record. Generators never read
held-out data or build-specific answers.
"""
import hashlib
import json
import math

TASK = "numpy-quantile"
EPS = 8 * 2.220446049250313e-16  # protected comparator: 8 * eps(float64)
FLOAT_DTYPES = ["float64", "float32"]
OTHER_DTYPES = ["float16", "longdouble", "int8", "int16", "int32", "int64", "uint8", "uint64",
                "bool", "complex128", "object"]
METHODS = ["inverted_cdf", "averaged_inverted_cdf", "closest_observation",
           "interpolated_inverted_cdf", "hazen", "weibull", "linear", "median_unbiased",
           "normal_unbiased", "lower", "higher", "midpoint", "nearest"]
FUNCS = ["quantile", "nanquantile", "percentile", "nanpercentile", "median", "nanmedian"]
KINDS = ["normal", "nan_some", "all_nan", "signed_zeros", "inf_mix", "subnormal", "ties",
         "dup_heavy", "sorted", "reversed", "huge", "tiny", "mixed_mag", "constant"]
LAYOUTS = ["C", "F", "strided", "negative", "transposed", "unaligned", "byteswapped",
           "readonly", "broadcast"]
SIZES = [0, 1, 2, 3, 7, 1023, 1024, 1025, 32767, 32768, 32769, 65535, 65536, 65537]
SEQUENCES = ["single", "aba_retain_outputs", "interleaved_instances", "repeat_same_input"]


def _case_id(case):
    body = json.dumps({k: v for k, v in case.items() if k != "case_sha256"}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def generate(rng, budget):
    """Bounded, deterministic iterable of cases; rng is random.Random."""
    for _ in range(int(budget)):
        func = rng.choice(FUNCS)
        dtype = rng.choice(FLOAT_DTYPES * 4 + OTHER_DTYPES)
        ndim = rng.choice([0, 1, 1, 2, 2, 3])
        n = rng.choice(SIZES)
        shape = [] if ndim == 0 else [n] + [rng.choice([1, 2, 3, 5, 8]) for _ in range(ndim - 1)]
        rng.shuffle(shape)
        axis_choices = [None] + list(range(-ndim, ndim)) + ([tuple(range(ndim))] if ndim > 1 else [])
        axis = rng.choice(axis_choices) if ndim else None
        q_form = rng.choice(["scalar", "list", "array_f4", "edge", "invalid"])
        case = {
            "task": TASK,
            "surface_id": f"numpy.{func}",
            "contract_id": "numpy-quantile/instruction#workload-and-correctness",
            "precondition_id": "public-documented-call",
            "input": {"dtype": dtype, "shape": shape, "kind": rng.choice(KINDS),
                      "layout": rng.choice(LAYOUTS), "seed": rng.randrange(2 ** 31)},
            "options": {"q_form": q_form, "q_seed": rng.randrange(2 ** 31),
                        "axis": list(axis) if isinstance(axis, tuple) else axis,
                        "keepdims": rng.choice([False, True]),
                        "method": rng.choice(["linear"] * 6 + METHODS)
                        if func in ("quantile", "nanquantile", "percentile", "nanpercentile") else None,
                        "out": rng.random() < 0.05,
                        "overwrite_input": rng.random() < 0.05},
            "sequence": rng.choice(SEQUENCES),
            "faults": None,
        }
        case["case_sha256"] = _case_id(case)
        yield case


def precondition(case, surface):
    """Reject undefined/private API states; expected library errors stay valid."""
    func = case["surface_id"].split(".")[-1]
    if func not in FUNCS:
        return {"valid": False, "reason": "not a documented public entry point"}
    opts = case["options"]
    if opts.get("method") is not None and func in ("median", "nanmedian"):
        return {"valid": False, "reason": "median family has no method parameter"}
    if case["input"]["layout"] == "broadcast" and opts.get("overwrite_input"):
        return {"valid": False, "reason": "overwrite_input on a read-only broadcast view is "
                                           "a documented error path tested separately"}
    return {"valid": True, "reason": "public documented call; errors are valid outcomes"}


def shrink(case):
    """Bounded shrink steps preserving surface, contract, layout class and faults."""
    shape = case["input"]["shape"]
    for i, n in enumerate(shape):
        if n > 1:
            for smaller in (n // 2, n - 1, 1):
                c = json.loads(json.dumps(case))
                c["input"]["shape"][i] = smaller
                c["case_sha256"] = _case_id(c)
                yield c
    if case["sequence"] != "single":
        c = json.loads(json.dumps(case)); c["sequence"] = "single"; c["case_sha256"] = _case_id(c)
        yield c
    if case["options"].get("keepdims"):
        c = json.loads(json.dumps(case)); c["options"]["keepdims"] = False; c["case_sha256"] = _case_id(c)
        yield c


def _float_equal(x, y):
    # Same rule as environment/workload/task.py compare(); ±0.0 compare equal.
    return (math.isnan(x) and math.isnan(y)) or x == y or (
        math.isfinite(x) and math.isfinite(y) and abs(x - y) <= EPS + EPS * abs(x))


def _values_equal(a, b):
    if a["dtype"] != b["dtype"] or a["shape"] != b["shape"]:
        return False, "dtype/shape"
    if a["encoding"] != b["encoding"]:
        return False, "encoding"
    if a["encoding"] == "float":
        ok = len(a["values"]) == len(b["values"]) and all(
            _float_equal(float(x), float(y)) for x, y in zip(a["values"], b["values"]))
        return ok, "float-8eps"
    # Integers/bools/complex parts/object reprs: exact semantic values, never raw .npy bytes.
    return a["values"] == b["values"], "exact-semantic"


def compare(baseline, candidate, case):
    """Both-arm oracle over observation records (optimbench-fuzz-observation-v1)."""
    diffs = []
    bt, ct = baseline["termination"], candidate["termination"]
    if bt["kind"] != ct["kind"]:
        diffs.append({"field": "termination", "baseline": bt, "candidate": ct})
    bo, co = baseline["outcome"], candidate["outcome"]
    calls_b, calls_c = bo.get("calls", []), co.get("calls", [])
    if len(calls_b) != len(calls_c):
        diffs.append({"field": "call_count", "baseline": len(calls_b), "candidate": len(calls_c)})
    for i, (x, y) in enumerate(zip(calls_b, calls_c)):
        if x["status"] != y["status"]:
            diffs.append({"field": f"call[{i}].status", "baseline": x["status"], "candidate": y["status"]})
            continue
        if x["status"] == "error":
            # Exact type; exact message for validation errors (contractual),
            # type-only for MemoryError (must-not-flag: allocator text).
            if x["type"] != y["type"] or (x["type"] != "MemoryError" and x["message"] != y["message"]):
                diffs.append({"field": f"call[{i}].error", "baseline": x, "candidate": y})
        else:
            ok, policy = _values_equal(x["value"], y["value"])
            if not ok:
                diffs.append({"field": f"call[{i}].value", "policy": policy})
        if x["warnings"] != y["warnings"]:
            diffs.append({"field": f"call[{i}].warnings", "baseline": x["warnings"], "candidate": y["warnings"]})
    for key in ("input_unchanged", "earlier_outputs_unchanged", "threads_after_equal_before"):
        be, ce = baseline["effects"].get(key), candidate["effects"].get(key)
        if ce is not True and be is True:
            diffs.append({"field": f"effects.{key}", "baseline": be, "candidate": ce})
    return {"equal": not diffs, "policy_id": "numpy-quantile/protected-8eps-v1", "differences": diffs}
