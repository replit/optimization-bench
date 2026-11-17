# Make Terser faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Two build steps are measured. The first minifies 149 distinct original JavaScript assets from a fixed cdnjs catalog (15,926,819 input bytes). The second minifies 7 unminified ES-module library files from pinned npm packages (5,558,785 input bytes). Each step is one Node process that calls `minify({filename: source}, options)` once per input, in order. The inputs are read as source text and are never executed.

The cdnjs step uses one compression pass, mangling enabled, comments removed and source maps disabled. The ES-module step uses webpack 5's production defaults for a module asset: two compression passes, mangling, `module` and `ecma` 2015, plus a comments callback that extracts license comments. Unsafe options and property mangling are disabled. Startup, imports and the complete sequence of calls belong to the measured operation. The minimal environment and fresh scratch, home and temporary directories are fixed. Nothing may persist between workload invocations.

Every complete result (minified code, and for the ES-module step the extracted license text) must match the baseline UTF-8 bytes, with its length and SHA-256 recorded in the public manifest. Preserve all supported JavaScript syntax, API behavior, errors and options beyond those used here. The drivers import the built production source through `main.js`; an unchanged installed or distribution copy cannot substitute for the candidate.

## What you may edit
In `/app/terser`, only `.js` files under `lib` are editable. Limits are 1,000 files and 20,000,000 bytes. All other paths are protected, including `main.js`, package manifests and locks, build settings, dependencies, tests, fixtures, drivers, inputs and manifests. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source and dependencies; the authenticated public input files are supplied read-only at `/app/workload/inputs/files` before the offline environment starts, and input bodies are not stored in the image. These commands rebuild the candidate and check its public behavior and upstream tests; public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh
/app/build/run
/app/baseline-build/run
cd /app/build/project && npm test
```

Run tests from the built project so command-line tests use its freshly built distribution. The public runner reports each asset's result length and hash; `--outputs DIR` retains complete results in a fresh directory.

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
- Write only declared outputs.
