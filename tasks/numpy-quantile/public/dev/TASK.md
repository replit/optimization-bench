# Compute linear quantiles over arrays and axes

Given a resident floating-point array, requested quantiles and reduction axes, return the complete result of:

```python
np.quantile(a, np.asarray(q, dtype=np.float64), axis=axis,
            method="linear", keepdims=keepdims, overwrite_input=False)
```

For a sorted reduction slice of length `n`, let `h = (n - 1) * q`. Interpolate between the values at `floor(h)` and `ceil(h)`, with fractional weight `h - floor(h)`. For `[0, 10, 20, 30]` and `q=[0.25, 0.5]`, return `[7.5, 15]`.

## Declared corpus domain

- Unweighted `method="linear"`; float32 and float64 arrays; one, two or three dimensions.
- Real-data workloads contain 10,000–10,000,000 logical input elements. Small API examples are separate correctness/reference cases.
- Shape lengths vary within that element budget. Public shapes are examples, not an exhaustive list of permitted dimensions.
- Scalar (zero-dimensional) or one-dimensional q, explicitly represented as float64, in `[0, 1]`. This first corpus uses up to 17 quantiles, including endpoints, repeated q and unsorted q.
- `axis=None`, individual positive/negative axes, and tuples of axes; both keepdims settings.
- C-contiguous and Fortran-contiguous inputs, transposed views, and stepped views. Negative strides appear in a correctness example.
- `overwrite_input=False`, no supplied out buffer, no weights. Finite real-data performance inputs; small correctness examples cover NaN propagation.

Use the complete array and all requested quantiles. Preserve input values and the pinned baseline's output shape and dtype. NaNs propagate along affected reduction slices; this is not `nanquantile`. Pooled/spatial quantiles are unweighted over the supplied elements.

The checker requires exact shape/dtype and compares values with `rtol=atol=8*eps(float64)` and equal NaNs. This is a draft numerical envelope checked against NumPy 2.5.4 and full sorting on this corpus. It does not qualify every floating-point edge case or every NumPy API option. Signed-zero inputs are exercised, but zero sign bits and NaN payload bits are not required by this checker.

## Representative uses

Table summaries request quartiles or tails separately for each numerical column. Distance binning requests many quantiles from one long vector and retains equal boundaries where values tie. Demand summaries reduce a time-by-customer matrix along either axis. Generic 3D examples cover time reductions into maps, spatial tuple-axis reductions, and pooled reductions.

Data loading, source parsing, sampling, initial dtype conversion and construction of the specified input view are setup. The measured operation starts immediately before `np.quantile` and ends only after the complete output is returned. Any copying, partitioning, sorting, interpolation or layout conversion required by the implementation belongs inside that operation. No pre-sorted input representation or cached answers are supplied.

## Status

This packet establishes workload inputs and descriptions. The experimental source-build integration, editable scope, resources, timing boundary, and score rules are described in [EVALUATION.md](EVALUATION.md). Launch qualification is tracked separately by the organizer; the standalone checker does not establish it. Corpus examples alone do not cover every upstream API behavior, out buffer, invalid-input error, or infinity case. The host judge additionally runs the protected NumPy suite before scoring a submitted artifact.
