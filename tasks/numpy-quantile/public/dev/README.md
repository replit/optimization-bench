# NumPy linear quantile workloads — development packet

Compute quantiles over arrays and axes using the declared linear interpolation method.

This packet contains resident-array inputs, exact calls, provenance, and a runnable development checker. See [TASK.md](TASK.md) for the operation contract and [CASES.md](CASES.md) for every workload description.

## What is included

| Data | Development inputs | What the calls do |
|---|---|---|
| NYC yellow taxi | January and July 2023; one million selected rides per month | Distance medians and bin boundaries; quartiles and tails of individual numerical columns |
| UCI electricity | 148 fixed customers in 2012; two chronological blocks | Demand quantiles over time, across customers, and over the pooled matrix |
| Generic generated arrays | One 3D array and two small upstream-inspired examples | Demonstrate array axes, layouts and the linear method |
| Small correctness examples | Eight calls | Interpolation, ties, NaNs, scalar/repeated q, singleton axes, tuple axes, keepdims, negative strides and signed-zero inputs |

The 37 calls comprise 22 real-data workloads, five generic 3D coverage examples, two public benchmark-style references and eight correctness examples. Synthetic examples demonstrate mechanics; they are not evidence of application frequency. Related views are variants of the same input, not independent datasets.

The evaluation design also reserves an entire external source dataset from development. Held-out evaluation uses the same declared array/API domain. Its inputs, manifest and source notices are not in this packet or the agent image; they ship with the verifier in the task's `tests/` directory (input bodies in Git LFS). Public availability of an underlying source does not make it unseen training data.

## Run

Use Python 3.12 or later and NumPy 2.5.4; the standalone checker was verified with Python 3.13. NumPy is the only runtime dependency. The experimental source-build evaluator uses Python 3.12 and is described in [EVALUATION.md](EVALUATION.md).

Materialize this packet with `environment/prepare_public.py` first; see the task README.

```bash
python -m pip install -r requirements.txt
python run.py --reference
python run.py --case taxi --repeat 3 --report taxi-check.json
```

The prepared packet's `run.py` verifies input hashes, checks shape/dtype/values and input preservation, and optionally computes a full-sort reference. It reports local operation timings for convenience. Those numbers have no CPU isolation, paired baseline, warm-up policy, interference checks or aggregate score.

Loading arrays, constructing the declared view, converting dtype, and checking results happen outside the timer. All work performed by `np.quantile`, including its required copies and complete output allocation, is inside. A view or layout conversion listed in a case defines the supplied input; an implementation's own conversions belong inside the call.

## Files

- `manifest.json`: input hashes, provenance groups, exact preparation, strides, q, axis and expected output metadata.
- `arrays/`: NumPy input arrays and taxi source row indices, materialized from the separate public input bundle. Load with `allow_pickle=False`.
- `sources.json`: source receipts and transformations for the public parents only.
- `TASK.md`, `CASES.md`: task contract and case descriptions.
- `run.py`, `requirements.txt`: standalone checker and runtime dependency pin.

## Source attribution and transformations

[NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) supplies monthly trip records. We retain distance, fare, tip and total amount columns; filter for finite selected values and pickup timestamps inside the nominal month; then select one million evenly spaced eligible source-order rows. Negative, zero and unusually large finite values are retained. Source row indices and source-file SHA-256 values allow exact reconstruction. TLC cautions that it does not guarantee the records' accuracy.

Artur Trindade (2015), [ElectricityLoadDiagrams20112014, UCI Machine Learning Repository](https://doi.org/10.24432/C58C86), is licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The provided arrays are adapted customer/year subsets, with layout and dtype variants described in the manifest. Measurements are quarter-hourly power values in kW. Inactive-customer and daylight-saving zeros remain; we do not impute, normalize or interpret the matrices as energy totals. Customer identifiers and timestamp ranges are recorded for each public parent.

The ordered-vector and small-table references adapt shapes and q values from the [NumPy percentile benchmarks](https://github.com/numpy/numpy/blob/main/benchmarks/benchmarks/bench_function_base.py) and [pandas frame quantile benchmarks](https://github.com/pandas-dev/pandas/blob/main/asv_bench/benchmarks/frame_methods.py). Input generation and harness code here are newly written; the original benchmark source code is not bundled.

Stored reference outputs are not distributed. Local full-sort checks share the candidate interpreter; the hosted numerical comparator runs separately.
