# numpy-quantile fuzzing (v1.1 task owner)

Status: readiness skeleton. Nothing here is a pass claim. Files are draft until
the adapter, surface inventory and controls land.

Scope plan (owner brief, all ten principles):

- Surface: documented public quantile family (`quantile`, `nanquantile`,
  `percentile`, `nanpercentile`, `median`, `nanmedian`, `ndarray.partition`/
  `np.partition` and the private `_quantile_linear`-style candidate entry points
  only through public calls). A corrected transitive extraction will provide
  the final denominator.
- Protected comparator: unchanged dtype/shape/8eps/equal-NaN rules. Integers
  are compared semantically, not by raw `.npy` header bytes (Validation2
  note). Storage-order variation and the sign of returned zeros are
  must-not-flag (contract-permitted; evidence from the sweep's review).
- State: retain live outputs across A/B/A sequences and re-check earlier
  results after later calls (aliasing of reused output buffers, per Validation2).
- Faults: allocator injection over every large allocation (LD_PRELOAD),
  KeyboardInterrupt during long calls, read-only/shared/strided inputs.
- Threads: 1/2/8 CPUs with varied affinity, five repetitions, per-call OS
  thread lifecycle; TSan build for thread-creating candidates.
- Native: ASan+UBSan builds of pristine/candidate; MSan feasibility recorded.
- Coverage: Python (coverage.py, branch) + C/C++ (gcov/llvm-cov) on changed
  lines/branches per candidate.
- Mutants: at least 20 compiled, non-equivalent disposable pristine mutants,
  85% kill target, mandatory real-regression controls.
- Header-consumer matrix: public C headers under `numpy/_core/include/numpy`;
  supported dialects from pinned upstream build/CI docs before any candidate
  result. If out of task scope, record scope evidence for integrator review.

Correctness-only cases never enter performance statistics.
