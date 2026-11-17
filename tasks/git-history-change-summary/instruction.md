# Make Git history summaries faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Seven units of normal Git command-line work over distinct original repositories; no input repeats across units. Three units run `git diff-tree --stdin` over 32,768 commits from four repositories: full Myers patches for 16,384 commits, and Myers shortstats and histogram patches for 8,192 other commits each. Three more produce complete patches for distinct commits of other repositories with `--ignore-all-space`, the patience algorithm and `-U32767` context. The seventh runs `git merge-file -p -L ours -L base -L theirs` on base, ours and theirs blobs from real merge commits.

Each repository batch or merge triple gets one fresh Git process; normal `-C` arguments select the supplied checkout. Timing covers the Git processes from start to exit. Input fetch, authentication and merge-file blob extraction by the original build happen beforehand. The supplied arguments, commit lists, triples and repository data remain fixed.

Complete stdout and exit status of every process must match exactly; patch bytes may not be truncated or replaced by summaries. Error messages/classes and exit status must match; stack traces and memory addresses may differ. Preserve xdiff declarations, structures, callbacks, ownership and general Git command-line behavior. The image's existing ownership-related configuration is fixed, not permission to change runtime settings.

## What you may edit
In `/app/git`, only these 15 existing `.c` and `.h` files are editable: `xdiff/xdiff.h`, `xdiff/xdiffi.c`, `xdiff/xdiffi.h`, `xdiff/xemit.c`, `xdiff/xemit.h`, `xdiff/xhistogram.c`, `xdiff/xinclude.h`, `xdiff/xmacros.h`, `xdiff/xmerge.c`, `xdiff/xpatience.c`, `xdiff/xprepare.c`, `xdiff/xprepare.h`, `xdiff/xtypes.h`, `xdiff/xutils.c`, `xdiff/xutils.h`. Modification is allowed; adding or removing files is not. Limits are 15 files and 2,000,000 bytes. All other source, build scripts, tests, fixtures, dependencies, drivers, inputs and configuration are protected. Add no dependencies. Existing attribution must remain intact.

## Build and check
The image contains the pinned source, dependencies and public inputs. These commands rebuild the candidate and check its public behavior and the selected upstream suites (pristine outcomes, including failures, must stay unchanged); public output checks do not report evaluation timings.

```sh
sh /app/workload/build.sh /app/build
/app/build/run
export GIT_TEST_INSTALLED=/app/build/project GIT_EXEC_PATH=/app/build/project
make -C /app/build/project CFLAGS='-O3 -g' NO_TCLTK=YesPlease GIT-BUILD-OPTIONS
GIT_SKIP_TESTS=t4015.129 make -C /app/build/project/t GIT_TEST_OPTS=--tee 'T=t4000-diff-format.sh t4002-diff-basic.sh t4033-diff-patience.sh t4048-diff-combined-binary.sh t4050-diff-histogram.sh t4015-diff-whitespace.sh t4055-diff-context.sh t6403-merge-file.sh t6427-diff3-conflict-markers.sh' test
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
- Programs that include the public headers must still compile and behave as with the original.
