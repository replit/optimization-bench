"""Fresh-process quantiles over real source fragments and independent arrays/views."""
import base64
import io
import json
from pathlib import Path
import sys
import warnings

sys.path.insert(0, "/app/build/site")
import numpy as np

case = json.loads(Path(sys.argv[1]).read_text())
sources = sorted(Path("/app/workload/inputs/data/arrays").glob("*.npy"))
source = np.load(sources[case["source"] % len(sources)], mmap_mode="r", allow_pickle=False)
flat = np.asarray(source).reshape(-1)
size = int(np.prod(case["shape"]))
values = np.resize(flat[case["offset"]:case["offset"] + size], size)
if values.size == 0:
    raise ValueError("Empty frozen source fragment")
a = np.asarray(values + case["salt"], dtype=case["dtype"]).reshape(case["shape"])
if case["nan"] and a.dtype.kind == "f":
    a.flat[0] = np.nan
if case["view"] == 1:
    a = a[..., ::-1]
elif case["view"] == 2:
    a = np.asfortranarray(a)
if case["empty"]:
    a = a[:0]
before = a.tobytes()
outputs, arrays = [], []
for q in (case["q"], [.5], case["q"]):
    # Independent calls interleave an unrelated array and retain all results.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            value = np.quantile(a, q, axis=case["axis"], keepdims=case["keepdims"],
                                method=case["method"], overwrite_input=False)
            stream = io.BytesIO()
            np.save(stream, value, allow_pickle=False)
            arrays.append(base64.b64encode(stream.getvalue()).decode())
            outcome = {"status": "ok", "dtype": str(value.dtype), "shape": value.shape}
            # Returned storage must not mutate the source or later results.
            if value.size:
                value.flat[0] = 123
        except Exception as error:
            outcome = {"status": "error", "type": type(error).__name__, "message": str(error)}
        outcome["warnings"] = [{"type": type(w.message).__name__, "message": str(w.message)}
                               for w in caught]
        outputs.append(outcome)
    np.quantile(np.array([7., -1., 3.]), [.25, .75], method="linear")
print(json.dumps({"calls": outputs, "arrays": arrays, "input_unchanged": before == a.tobytes()}))
