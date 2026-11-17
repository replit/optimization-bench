# Make sqlfmt faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
One image contains two frozen timed units, the original CLI `batch` and
`native-batch`. Both count equally toward aggregate speedup, not per-document
scores. The native batch is scored, not an optional diagnostic.

Native uses the same rebuilt source as the CLI, calling `format.Format` once
for each of 844 public documents. Hidden evaluation uses a separate held-out
batch. Startup, decoding, formatting, complete result serialization, flushing
and exit are timed. Full outputs, errors and status must match.

The CLI unit sums its cold process intervals. Each invocation uses a fresh
directory and real PostgreSQL files. Flags cover tree diffs (`-d`), changed-file
lists (`-l`) and rewriting (`-w`), plus stdin and single-file formatting.

Preparation, timestamp normalization, comparison and cleanup are untimed.
Public inputs in `/app/workload/inputs/` define arguments, input kinds and
declared outputs. Keep the supplied environment, working-directory home,
C locale and UTC timezone fixed.

Exit status, stdout and declared outputs must match, including rewritten
trees. CLI error messages/classes must match, though stack traces and memory
addresses may differ. Preserve all supported behavior and flags, including
those not represented publicly.

## What you may edit
In `/app/sqlfmt`, only non-test `.go` files under `format` and `cmd/sqlfmt` are editable. `*_test.go` and `cmd/sqlfmt/explaincmd.go` are protected. Limits are 97 files and 407,847 bytes; source growth may not exceed 64 KB. All other paths are protected, including module files, build settings, tests, fixtures, dependencies, drivers and inputs. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source and dependencies; the authenticated public input files are supplied read-only at `/app/workload/inputs/files` before the offline environment starts, and input bodies are not stored in the image. These commands rebuild the candidate and check its public behavior and upstream tests; public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh
/app/build/run
python3 /app/workload/check_public.py /app/build
cd /app/sqlfmt && go test -count=1 ./...
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
- Read only named input files or stdin; write only declared outputs. Do not add environment-driven behavior.
- Do not use `unsafe`, `syscall`, `runtime/debug`, hashing, crypto, compression, or base64/hex encoding packages.
