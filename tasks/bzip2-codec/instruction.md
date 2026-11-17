# Make bzip2 faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Compress and decompress complete natural files at block-size settings 1 and 9. The driver uses streaming `BZ2_bzCompress` and `BZ2_bzDecompress` calls, fixed 64 KiB I/O chunks and fresh codec state per object. The supplied block sizes, work factor and stream call sequence are fixed.

One native batch executes each distinct original codec operation once. Timing includes process startup, file I/O, allocation, checksums, finalization and flushed output. Container startup, input staging and host verification occur outside timing. Individual operations and malformed-input cases also remain correctness checks.

A second native batch decodes 75 complete text archives with
`BZ2_bzDecompressInit(..., small=1)`, block-size setting 9 and the same 64 KiB
chunks. Each file uses fresh state. Both batches use one candidate library
build. Individual text decodes, malformed streams and a
library-allocation-cap request remain mandatory correctness checks.

Each encoded stream must match the baseline byte for byte; decompression must produce the exact original bytes. Preserve CRC verification, full input consumption, error behavior and supported API semantics. A stream that decompresses correctly but differs from the required encoded bytes is not an acceptable substitute. Public complete-result checks and inputs are available under `/app/workload`.

## What you may edit
In `/app/bzip2`, only these eight existing `.c` and `.h` files are editable: `blocksort.c`, `huffman.c`, `crctable.c`, `randtable.c`, `compress.c`, `decompress.c`, `bzlib.c`, `bzlib_private.h`. Modification is allowed; adding or removing source files is not. The command-line tool and helper programs, including `bzip2.c` and `bzip2recover.c`, are not editable. Limits are eight files and 4,194,304 bytes.

All other paths are protected, including `bzlib.h`, `Makefile`, `tests/**`, `driver/**`, `tools/**`, build settings, dependencies, inputs and manifests. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source, dependencies and public inputs. The following commands rebuild and check public behavior; public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh
/app/build/run
/app/build/run --outputs /tmp/candidate-output
```

The output option retains complete results in a fresh directory. The evaluator also runs the project's original tests against the rebuilt implementation, including supported behavior not exercised by the public file batch.

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
- For invalid arguments, missing files and unwritable outputs, the command-line program's error messages and exit status must match the original.
