# Make SQLGlot faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
One cold process transpiles all statements in 284 unchanged public Mozilla SQL files:

```python
sqlglot.transpile(text, read="bigquery", write="duckdb", pretty=False,
                 error_level=sqlglot.ErrorLevel.RAISE,
                 unsupported_level=sqlglot.ErrorLevel.RAISE)
```

Timing includes startup, imports, reads, calls and complete serialization. Preparation precedes execution. There is no database execution. Query order may vary.

Complete outputs must match byte for byte as compact UTF-8 JSON with `ensure_ascii=False` and separators `(",", ":")`. Preserve error types/messages, warnings, public APIs and dialect behavior. No stdout/stderr side effects are allowed.

Three cold batches use that build. Every request runs once:

- Optimizer: 125 public SQLMesh, documentation, TPC-H and TPC-DS requests.
  Parse, normalize, quote, then apply all `RULES` through `optimize` with
  `infer_schema=True`, `quote_identifiers=True`, `db=catalog=None`.
  Return complete SQL and typed trees.
- Column lineage: 125 public schema-bearing queries. Parse, copy, qualify,
  build scope and call `lineage` per column with `trim_selects=False`, `copy=False`.
  Return complete ordered graphs, sharing, payloads and semantic expressions.
- Structural diff: 99 public TPC-DS before/after pairs. Normalize in the source
  dialect, then call `diff` with `delta_only=False`, `f=t=0.6`. Return SQL,
  complete typed edits, endpoint paths, trees, node counts and similarity scores.

Protected drivers define exact settings and complete JSON contracts, retaining
types, comments, metadata, errors, logs and warnings. Allocation identity,
mapping order and incidental edit-list order are normalized, not graph order.

The four batches are equally weighted performance units under existing policy.
Startup through complete writes is timed. Nothing may be deferred beyond exit.
Every unit must pass from one build.

## What you may edit
In `/app/sqlglot`, only `.py` files under `sqlglot` are editable. Limits are 2,048 files and 33,554,432 bytes. All other paths are protected, including packaging/version settings, interpreter, native extensions, dependencies, tests, fixtures, drivers and input manifests. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source and dependencies; public input files are mounted read-only at `/app/workload/inputs/files`, not stored in the image. These commands rebuild the candidate and check its public behavior and upstream tests; public output checks do not report evaluation timings.

Use a fresh directory per check; never rebuild into one in use. Keep source unchanged during the build.

```sh
(
  set -eu
  build=$(mktemp -d /tmp/sqlglot-check.XXXXXX)
  printf 'Build/check directory: %s\n' "$build"
  sh /app/workload/build.sh "$build"
  "$build/run"
  /app/baseline-build/run
  "$build/run" --outputs "$build/results"

  cd /tmp
  status=0
  SKIP_INTEGRATION=1 PYTHONPATH="$build/runtime:/app/sqlglot" \
    python -c 'import unittest, sqlglot; unittest.main(module=None)' \
    discover -s /app/sqlglot/tests -t /app/sqlglot \
    > "$build/upstream-tests.log" 2>&1 || status=$?
  tail -n 20 "$build/upstream-tests.log"
  exit "$status"
)
```

Import the built package before test discovery changes Python's search path. Printing the log must not hide the test's exit status. SQLGlot's TPC fixtures are not distributed, so `test_tpch` and `test_tpcds` fail here; the evaluator excludes only those two tests.

The public manifest records expected output lengths and hashes. The output option retains complete results and batch streams in a fresh directory. The build directory also retains the upstream-test log.

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
- Write only within declared scratch and output directories.
