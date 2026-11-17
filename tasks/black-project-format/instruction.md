# Make Black faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Each operation runs in a cold process on a fresh original project tree with its own configuration. One public case checks SQLAlchemy with its CI command, `python -m black --check ./lib/ ./test/ ./examples/ setup.py doc/build/conf.py`, on a tree that still has formatting violations. A second reformats Django in place with `python -m black --quiet --line-length 79 .`. A correctness-only case runs `python -m black --check .` on an empty project.

Two more operations each run in one warm process and time only their call loop: `black.format_str` once per documentation code fragment (3,765, each with its project's mode), and `black.format_file_contents(text, fast=False, mode=Mode(), lines=ranges)` once per editor range request (9). Each call must do its own work; no memoization across calls.

Timing includes startup, imports, configuration discovery and file I/O through process exit. Each selected path is processed once per operation. The supplied environment and fresh configuration directories are fixed. Public trees are under `/app/projects`; cases and expected result lengths and hashes are under `/app/workload/inputs/`.

Exit status, stdout, final file bytes and symlinks must match exactly; so must every per-call API result, including `NothingChanged` and exceptions. Error messages/classes and exit status must match; stack traces and memory addresses may differ. Preserve other target versions, preview features and public APIs as well. The build must remain native mypyc with `black.COMPILED` true.

## What you may edit
In `/app/black`, only `.py` files under `src/black`, `src/blib2to3` and `src/blackd` are editable. Limits are 1,000 files and 20,000,000 bytes. All other paths are protected, including grammars, packaging, build settings, dependencies, tests, fixtures, drivers and input trees. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source, dependencies and public inputs. These commands rebuild the candidate and check its public behavior and upstream tests; public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh
/app/build/run
/app/baseline-build/run
cd /app/black && PYTHONPATH=/app/build/runtime python -m pytest tests -k "not incompatible_with_mypyc" -n 8
```

The evaluator also runs the mypyc-incompatible tests against the same source as pure Python.

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
- Read only what Black normally reads and write only declared outputs.
