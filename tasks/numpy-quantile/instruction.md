# Make NumPy faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Seven timed units each run one resident batch of NumPy calls on prepared real-data inputs: `np.quantile(a, q, axis=axis, method="linear", keepdims=keepdims, overwrite_input=False)` over varied shapes, axes, quantiles and memory layouts; `np.interp`; `np.busday_count`, `np.busday_offset` and `np.is_busday`; ISO-8601 parsing and formatting of `datetime64[ms]`; `Generator(PCG64)` binomial, gamma, Poisson and normal draws; float `astype(str)`; and `np.loadtxt` of CSV text.

The `strings-text` and `genfromtxt-catalog` batches remain correctness-only checks of `np.strings` and `np.genfromtxt`, respectively; they are not timed units. `strings-text` has no public batch and is checked on held-out data only. Public quantile cases also check NaNs, signed zeros, tuple axes and negative strides. All supplied inputs and correctness checks remain in scope.

Timing covers each complete call batch and result materialization. Input loading, argument preparation, result canonicalization and comparison are outside timing. Keep the supplied batch composition and input buffers unchanged.

Quantile results must have exact shapes and dtypes, with `rtol = atol = 8 * eps(float64)` and equal NaNs. `np.interp` results allow an absolute error of `8 * eps(float64)`, `np.loadtxt` NaNs compare equal, and all other results must match exactly after canonical ordering where NumPy leaves order unspecified. Input buffers must be preserved. A different output hash alone is not a correctness failure within these tolerances. Preserve the full supported API, argument validation and error behavior, not just the listed calls.

## What you may edit
In `/app/numpy`, production files under `numpy` are editable only with these extensions: `.py`, `.cpp`, `.c`, `.h`, `.hpp`, `.cc`, `.hh`, `.cxx`, `.hxx`, `.pyi`, `.pyx`, `.pxd`, `.pxi`, `.src`, `.inc`, `.in`, `.template`, `.s`, `.S`, `.f`, `.f90`, `.f95`, `.pyf`.

Protected paths/patterns take precedence: `setup.py`, `meson.build`, `meson.options`, `**/tests/**`, `**/test/**`, `numpy/testing/**`, `test_*`, `*_test.*`, `*_tests.*`, `conftest.py`. Limits are 10,000 files and 67,108,864 bytes. All other paths, build settings, dependencies, drivers and inputs are protected. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source, dependencies and public inputs. The following commands rebuild and check public behavior; public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh
/app/build/run
/app/build/run --outputs /tmp/candidate-output
cd /tmp && PYTHONPATH=/app/build/site python3 -m pytest --pyargs numpy -n 8
```

Use a fresh output directory to retain complete results. The evaluator runs the project's original tests against the rebuilt package in addition to numerical output checks.

## How it is evaluated
A separate clean environment rebuilds your editable source, checks the edit scope, runs the project's tests, and compares outputs on public inputs and hidden inputs of the same kind using the contract above. It then times the original and your version on the hidden inputs, checking every output. Your score is a conservative estimate of the wall-clock speedup on the hidden inputs; when there are several timed units, each counts equally. Total CPU time and peak memory are reported but not scored.

## You may
- Change any code in the editable paths, including algorithms, data structures and internal interfaces.
- Add to the public interface, as long as every existing use keeps working.
- Use all 8 CPUs and 16 GiB, as long as results do not depend on how many CPUs are available.

## Rules
- Preserve all supported behavior, validation, errors and the public interface; compatible additions are allowed.
- Do not modify, remove or weaken existing tests. Changes outside the editable paths are ignored.
- No caching across calls or processes, no input or evaluator recognition, no precomputed results, and no delegation to other libraries or programs.
- Do not change process-wide settings, including GC, allocator, environment, signal handlers, locale, working directory, resource limits or CPU affinity.
- When a public call returns (or the program exits, for command-line workloads), nothing it started may still be running.
- Do not change the workload's fixed settings, flags, inputs, operation order or measurement boundaries. Do not defer required work beyond the measured operation.
- Results must be the same on every run, on any number of CPUs, and across repeated calls in the same process.
- Overlapping or aliased arguments, and short or failed reads and writes, must behave as in the original.
- No memory errors, undefined behavior or crashes, including on valid inputs the workload does not use.
- Do not use GPU offload or replace the implementation with another dependency.
- Programs that include the public headers must still compile and behave as with the original.
