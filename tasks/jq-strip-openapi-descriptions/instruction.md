# Make jq faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Four equally weighted units share one build and image. Acquisition, decompression, staging, input checks and result comparison are outside timing.

The native unit processes 30 public OpenAPI documents. Each gets a fresh jq state, filter compilation, complete parse, evaluation and teardown:

```jq
walk(if type == "object" then del(.description) else . end)
```

Results use `jv_dump_string` with flags zero and a newline. Complete output/error bytes must match. Timing includes reads, compilation, parsing, evaluation and writes.

The GeoJSON unit uses the CLI:

```sh
jq -cn --stream 'fromstream(2 | truncate_stream(inputs | select(.[0][0] == "features")))' input.geojson
```

Preserve empty/scalar features and partial output before parse errors. `.features[]` is not equivalent.

The grouping unit counts repository/event-type pairs in one complete GH Archive hour, sorting by descending count, repository and type:

```sh
jq -nc '[inputs | [.repo.name, .type]] | group_by(.) | map({repository: .[0][0], event_type: .[0][1], count: length}) | sort_by([-.count, .repository, .event_type])' events.jsonl
```

The CSV unit independently reads that complete hour, emitting each event in input order:

```sh
jq -r '[.id, .type, .repo.name, .created_at] | @csv' events.jsonl
```

Each CLI unit runs fresh with default buffering. Timing covers reads, compilation, parsing, evaluation and output. Complete stdout, stderr and exit status must match. No input repetition or cross-unit cache is used. All supported jq filters and public APIs remain part of the contract.

## What you may edit
In `/app/jq`, only `.c`, `.h`, `.jq`, `.l` and `.y` files under `src` are editable, except protected `src/jq_test.c`. Limits are 1,000 files and 20,000,000 bytes. All other paths are protected, including build scripts, dependencies, tests, fixtures, drivers, input files and manifests. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source and dependencies. The operator supplies the authenticated public input files as a read-only runtime packet at `/app/workload/inputs/files` before the offline environment starts; input bodies are not stored in the image. These commands rebuild the candidate and check its public behavior; public output checks do not report evaluation timings. The evaluator runs the original upstream tests separately.

```sh
sh /app/workload/build.sh
/app/build/run
```

The build requires a fresh output directory. For subsequent builds, pass a new directory to the build script and use that directory for the run command.

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
