"""Documented public-surface inventory for numpy-quantile (pristine build).

Usage: python3 inventory.py <site-dir> <output.json>

Records each documented public entry point, its signature, defining module and
every public alias that resolves to the same object (re-export chain), plus the
option families the generators must cover. This is a documented-API inventory,
not a corrected transitive extraction; the
output says so explicitly and makes no completeness claim.
"""
import hashlib
import inspect
import json
import sys

site, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, site)
import numpy as np  # noqa: E402

assert np.__file__.startswith(site), np.__file__

# Entry points whose documented behavior reaches the quantile/selection code
# paths that the task's editable root (numpy/) can change. Sort/partition are
# included because the selection and x86-simd-sort sources are editable and
# shared with them.
ENTRY_POINTS = [
    "quantile", "nanquantile", "percentile", "nanpercentile", "median", "nanmedian",
    "partition", "argpartition", "sort", "argsort",
]
METHODS = ["ndarray.partition", "ndarray.argpartition", "ndarray.sort", "ndarray.argsort"]
NAMESPACES = ["numpy", "numpy.lib", "numpy.ma", "numpy.matlib"]

OPTION_FAMILIES = {
    "quantile": ["a", "q", "axis", "out", "overwrite_input", "method", "keepdims", "weights",
                 "interpolation(deprecated)"],
    "methods": ["inverted_cdf", "averaged_inverted_cdf", "closest_observation",
                "interpolated_inverted_cdf", "hazen", "weibull", "linear", "median_unbiased",
                "normal_unbiased", "lower", "higher", "midpoint", "nearest"],
    "input_domains": ["dtype (float16/32/64/longdouble, ints, bool, complex, object, datetime)",
                      "byte order", "alignment", "memory layout (C/F/strided/negative/broadcast)",
                      "ndim 0..N", "empty axes", "NaN/inf/±0/subnormal", "array-likes and subclasses"],
    "q_domains": ["scalar", "1-d", ">1-d (error)", "out of [0,1] (error)", "NaN", "dtype/weak promotion"],
    "partition": ["kth int/sequence/negative/out of range", "axis", "kind", "order (structured)"],
}


def aliases(obj):
    found = []
    for ns_name in NAMESPACES:
        try:
            ns = __import__(ns_name, fromlist=["_"])
        except Exception:
            continue
        for name in dir(ns):
            if name.startswith("_"):
                continue
            try:
                if getattr(ns, name) is obj:
                    found.append(f"{ns_name}.{name}")
            except Exception:
                continue
    return sorted(found)


def describe(path, obj):
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        sig = None
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", path)
    impl = getattr(obj, "__wrapped__", None)
    return {
        "symbol": path,
        "defining_module": module,
        "qualname": qualname,
        "signature": sig,
        "dispatcher": impl is not None,
        "implementation_module": getattr(impl, "__module__", None) if impl else module,
        "aliases": aliases(obj) if path.startswith("numpy.") and "ndarray." not in path else [],
        "doc_sha256": hashlib.sha256((obj.__doc__ or "").encode()).hexdigest(),
    }


entries = []
for name in ENTRY_POINTS:
    entries.append(describe(f"numpy.{name}", getattr(np, name)))
for path in METHODS:
    entries.append(describe(f"numpy.{path}", getattr(np.ndarray, path.split(".")[1])))
result = {
    "format": "optimbench-fuzz-surface-inventory-v0",
    "task": "numpy-quantile",
    "numpy_version": np.__version__,
    "numpy_file": np.__file__,
    "extraction": {
        "kind": "documented-api",
        "status": "incomplete",
        "reason": "Corrected transitive re-export extraction not yet "
                  "applied; aliases are scanned only in " + ", ".join(NAMESPACES) + ".",
    },
    "entry_points": entries,
    "option_families": OPTION_FAMILIES,
}
with open(out, "w") as stream:
    json.dump(result, stream, indent=2, sort_keys=True)
    stream.write("\n")
print(json.dumps({"entries": len(entries), "numpy": np.__version__}))
