# NumPy evaluation contract

The 37-case corpus and local `run.py` remain useful independently. An optimization run must not be scored until the frozen workload plan and infrastructure qualification are complete.

## Source and correctness

The source build pins NumPy 2.5.4 and an immutable Python 3.12 compiler image. Production source throughout `numpy/` may be modified, added or removed under the extension and path rules in `/task/allowed.json`; there is no per-file allowlist. Avoid changing existing tests. Local test edits are excluded from submissions, and the host reconstructs production changes over the original tests and test support code. Build settings, dependencies, generated binaries and evaluation assets remain protected.

The host reconstructs and builds the submitted source offline. It runs NumPy's complete upstream test suite against each new artifact with four test workers. The suite covers API behavior outside the primary workload, including other methods and related operations. Tests declaring large memory requirements observe a 2 GB available-memory budget; their resource skips remain in the receipt.

Numerical workload results are checked outside the candidate process. Shape and dtype must match. Linear-quantile references are checked against full sorting. Values use `rtol=atol=8*eps(float64)` with equal NaNs. Inputs must remain unchanged. Partition outputs are checked for rank and multiset semantics; argpartition outputs must additionally be index permutations. A different valid partition order is permitted.

## Public workload families

Large vector, column, row, and multiaxis reductions are primary families. Small tables, the generic cube, benchmark-style examples, and the smaller stepped views are regression probes. Primary families have equal score weight rather than assigning one score weight per view. Within a family, timing is the total declared work. Families can share a worker lifetime while retaining individual timing and regression decisions.

The host also derives six multiaxis cases from the two public electricity arrays. Reshape each `(17568, 148)` block to `(366, 48, 148)` without repeating values. For each block, request `[0.1, 0.5, 0.9]` with `keepdims=True`, separately along `axis=0`, `axis=2`, and `axis=(0, 1)`. These represent distributions over consecutive 12-hour windows, customers, and pooled time.

Related-operation probes use a contiguous copy of the first public taxi month's distance column, retaining its values and ties:

- `median`, `percentile(q=[1, 50, 99], method='linear')`, `partition`, and `argpartition`, in float64, float32, and int32. Integer conversion truncates the real input values. Partition ranks are `n//4`, `n//2`, and `3*n//4`.
- `nanmedian`, `nanquantile(q=[.01, .5, .99], method='linear')`, and `nanpercentile(q=[1, 50, 99], method='linear')`, in float64 and float32, with every 101st element replaced by NaN.
- `quantile(q=[.01, .5, .99])` using `nearest`, `hazen`, and `inverted_cdf` as regression checks.
- Unchanged `sort` and `mean` calls as control workloads.

All calls preserve their inputs. Median and percentile variants use `overwrite_input=False`. Regression probes do not expand the optimization objective beyond linear quantile; they detect collateral changes.

## Timing, memory, and cores

Baseline-only calibration freezes repetition counts before candidate comparisons. The current `quarter-repeats-v1` policy divides those counts by four, rounding up, while retaining every case and array size. Warmup uses the same reduced counts. The sample-duration target is 2.5 seconds and the minimum-duration gate is 1.25 seconds; the 2% precision requirement is unchanged. Individual large-array calls must already involve meaningful work; repetition is for measurement stability. Correctness-only cases are not performance score inputs.

The trusted supervisor injects the current input bytes into a stopped worker. The external timer covers dispatch, the complete batch of calls, output materialization, result-address metadata, and confirmation that the worker is stopped. The supervisor then reads the complete results and verifies input preservation. Transfers and numeric checking are outside timing; required implementation work remains inside. Resident pages are initialized before each batch. This is not a claim of cold array caches.

Every shape and dtype receives a warmup batch. The frozen sequence then repeats identically for baseline and candidate. Outputs remain alive until inspection; old outputs are released during the next timed batch. Cross-call caching of computations or answers is prohibited. Whole-lifetime peak RSS, post-batch RSS/PSS, and growth traces include the supplied resident buffers and retained outputs. A 2 GB memory cap is enforced. Unexplained growth requires review and cannot qualify a score automatically.

The shared runner supplies physical-core and NUMA placement, sibling exclusion, the host evaluation lock and interference flags. Baseline samples are saved for the matching machine, toolchain, data and measurement setup, and measured only when missing. There are no repeated baseline-versus-baseline checks around each candidate. Intervals include variation in both saved baseline and fresh candidate samples. No noisy samples are silently discarded or retried. Development and final both run one, two and four cores, with two sessions per core count. Development overlaps the one- and two-core blocks on separate physical cores, then runs four-core blocks alone; at most four physical cores are active. Final blocks run sequentially. Concurrent development timings are diagnostics: separate cores still share memory bandwidth and caches. Their baseline cache entries are distinct from sequential final measurements. Default development and source submissions use two candidate samples per workload/core/session; `dev-bench --full` and final use four. The complete upstream suite runs with four test workers for each new artifact. Matching subsequent requests reuse the artifact and successful full-suite receipt; changing core counts does not rebuild or retest it. Native candidate threads can use assigned cores. BLAS/OpenMP defaults are one thread, and the harness does not split arrays among processes. More allocated cores do not imply NumPy itself scales.

## Decision rules

The primary score is the equal-weight geometric mean of family speedups across the two one-core sessions. Every primary and regression/control case at each tested core count must exclude a slowdown greater than 5% in both sessions. Timing quality gates still apply. A wide interval that overlaps the 5% regression boundary is inconclusive; an interval entirely beyond it in both valid sessions confirms a regression even when precision is insufficient to qualify a score. A confirmed slowdown in one operation cannot be canceled by a speedup in another. Numerical failure or upstream test failure rejects the artifact. Missing, noisy, short, or unresolved memory evidence produces no qualified score.

Held-out evaluation starts only after an immutable source nomination. It includes an external dataset withheld from development. Held-out identities, inputs, extraction recipes and feedback are not available to the agent during optimization; they ship with the verifier in the task's `tests/` directory, outside the agent image. Public baselines and diagnostics never mount the held-out corpus.
