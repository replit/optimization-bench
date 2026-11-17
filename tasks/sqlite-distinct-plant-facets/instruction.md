# Make SQLite faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Eleven timed units share one candidate, image and budget: ranked search and the
ten suite batches below. Plant facets, batch UPSERT and VACUUM remain
correctness-only cases, not timed units. Each request or incoming record occurs
once, and all supplied inputs and correctness checks remain in scope.

Plant facets (correctness only): country-filtered plant-ID ranges, all three country/owner/fuel
statements, BINARY collation, limit 31. Preserve complete ordered typed results.

Ranked search: real documentation and distinct question headings supply Boolean
OR queries. FTS5 uses unicode61, full detail and column sizes. Preserve complete
top-20 records, request identities, binary64 ranks, errors and lifecycle results.

Both read units use immutable databases, SQLite defaults and a 5000 ms busy
timeout. Stage requests first, without query warmup. Time SQL construction,
prepare/bind/step, accessors, buffering, finalize, cleanup and close. Export after
the result stop. Keep database bytes unchanged.

Batch UPSERT (correctness only): preload incoming records and seed odd-ID rows before timing.
Time one transaction, batches of at most 100 records, fresh prepare/bind/step,
finalize and commit. Existing conflicts are unchanged, so changed updates are
untested. Preserve local-only notes and every source field.

VACUUM (correctness only): start from the frozen post-retention database. Execute
prepare/step/finalize of VACUUM main once. Preserve all retained rows and require
actual compaction with no free pages.

Write-unit connections and inputs are staged before timing. Close, shutdown and
full-state verification follow the result stop. Preserve schema,
headers, types, settings and every ordered field. Leave no journal/WAL files.

Ten suite batches share that candidate, budget and timing rule; input staging
precedes ready and image export follows it: `PRAGMA integrity_check` over damaged
and sample databases; three `CREATE INDEX` statements; per-request JSON,
`printf`/`format`, R*Tree, `decimal_sum`, `percentile`, transit-join and
window-function queries with a 256 MiB page cache; and foreign-key cascade deletes
with triggers on a deserialized in-memory image. Compare complete typed rows;
exported database images must pass an independent integrity check and canonical
row dump.

## What you may edit
In `/app/sqlite`, every existing `.c` and `.h` file under `src` and `ext` is editable except files whose name contains `test`. This excludes the test-harness sources and permits exactly 229 files. Only modification is allowed; do not add or remove files. The source limit is 100,000,000 bytes.

All other paths are protected, including build settings, tests, fixtures, dependencies, adapters, SQL strings, request sets and original data. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source, dependencies and public inputs. These commands rebuild the candidate and check its public behavior; public output checks do not report evaluation timings. The evaluator runs the original upstream tests separately.

```sh
sh /app/workload/build.sh /app/build
/app/build/run
```

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
- Add no query-result, statement or derived-data caches beyond the protected staging and existing SQLite caches.
- Programs that include the public headers must still compile and behave as with the original.
