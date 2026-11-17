# Git RC library hooks and native CLI/header requests

Three verifier-owned fact callbacks and CLI/header requests are loaded by the
shared `judge.fuzzing_generic.task_runners(task)`. The shared runners assign
verdicts; this integration does not change timing/statistics or qualify a task.

`CLI_CASES` prepares two indexed, public-source text fixtures with trusted host
Git, then directly executes the admitted Git's normal worktree `diff` (Myers and
histogram). Unlike `diff --no-index`, normal `diff` succeeds with exit 0 when
differences exist. The shared runner must observe actual small and >=64 KiB
stdout and repeats its healthy/OS-stream-fault matrix without normalizing exit
codes. Fixture/precondition tests are not native IO-fault qualification.

`HEADER_CONSUMERS` registers two distinct linked TUs in both compiler-default C
and C++ modes, using the pinned xdiff header's explicit extern-C guard. The
consumers exercise `xdl_diff` callbacks and `xdl_merge` result ownership using
the admitted build's real Git/Rust libraries. Source hashes bind the upstream
declarations and default flags; the matrix must still succeed in the task image.
CI separately authenticates the pinned upstream source, verifies literal evidence
quotes, and checks the four consumer TUs' C/C++ syntax. That inexpensive check
does not link the task libraries or substitute for native matrix qualification.

## Contract basis and bounded surface

`instruction.md` explicitly requires preserving “xdiff declarations,
structures, callbacks, ownership and Git CLI behavior.” The task's existing
declaration gate names `project/xdiff/xdiff.h`. This is a **task-contracted xdiff
surface**, not a claim that Git ships a supported standalone installed SDK.
Pinned upstream `xdiff/xdiff.h` declares `xdl_diff`/`xdl_merge` and `out_line`;
`xdiff/xutils.c` propagates a negative emission callback as -1, and
`xdiff/xdiffi.c:xdl_diff` frees its environment and propagates that failure.
The probe uses the matching admitted headers and `libgit.a`, with
`git-compat-util.h` as the existing source consumer include precondition.

- `stream_faults`: nonempty diffs with `out_line` deliberately returning -1,
  under Myers, patience and histogram. Exact output/return/category are kept.
  This is not a POSIX stream callback: short-write/EINTR protocols are not
  invented. No OS-error text normalization is applied.
- `repeat_in_process`: 11 initialized bounded cases, including minimal,
  patience, histogram, whitespace, empty-side, missing-final-newline,
  emission failure and merge; the full sequence runs twice in one native
  process, with two fresh-process oracles per case and arm.
- `aliasing`: retain the first `xdl_merge` result across a second interleaved
  merge using swapped inputs, compare retained bytes and caller buffers, then
  free both results. Addresses are not compared across arms.

The output comparator is the shared exact typed comparator: byte output is
hex-encoded, and return codes and input/result ownership effects are exact.
Observer serialization uses verifier code, not candidate xdiff routines.

## Lifecycle and affinity

Each native operation has before/after fd/thread/child counts and concrete
TID, kernel start-tick, state, flags, TGID and scope identities. Threads use
native OS-thread ownership. Children are enumerated by visible `/proc/*/stat`
PPid because this isolated kernel omits `/proc/self/task/*/children`.
FD counts exclude the observer's temporary directory descriptor. Affinity is
read with `sched_getaffinity` **in the target process for every observation**.
The shared existing native process-state/runtime-configuration gate remains
controller-owned; this hook does not replace or waive it.

Raw compile outputs, exact argv, probe source/binary hashes, persistent/fresh
stdout/stderr, observations and shared-executor sidecars are retained.
The cache key includes image, CPU set, admitted build identities and output
directory. Different CPU sets cannot reuse facts.

## Replay

Focused tests:

```
python3 tasks/git-history-change-summary/tests/fuzz/test_rc_hooks.py
```

Native observer inside the unchanged task image, with matching rebuilt trees
mounted as `/baseline` and `/candidate` and writable `/scratch`:

```
python3 /verifier-fuzz/rc_driver.py \
  --baseline /baseline --candidate /candidate \
  --baseline-sha BASELINE_BUILD_INVENTORY_SHA256 \
  --candidate-sha CANDIDATE_BUILD_INVENTORY_SHA256 --out /scratch/facts
```

Official execution is via the shared task registration, not a manually
fabricated observation. `rc-smoke.json` binds the pristine-vs-pristine
shared-executor smoke to the frozen release-candidate (rc.1) judge and image; it is not an
optimized candidate replay or complete task qualification.

## Explicit gaps

Only the bounded library cases above have historical native receipts. The new
CLI/header requests require fresh native qualification; registration is not a
pass. This is not the full
six-family CLI differential suite or warm-object coverage. Partial callback
acceptance, allocator-failure injection, all xdiff options, arbitrary file
contents and races are not comprehensively explored. Complete corrected
surface authority, changed-line/branch coverage, sanitizer/mutation campaigns,
CLI/header execution and CPU-subset campaigns are **unmeasured** by those historical receipts.
No report-only feature is promoted to a score gate. No production task source,
protected workload, original tests, judge configuration or catalogs changed.
