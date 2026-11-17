# bzip2 RC registration (judge-v1.1-rc-generic)

`rc_hooks.py` registers facts only; the central controller assigns every verdict.

| Gate | Registration | Evidence source |
|---|---|---|
| stream_faults | `GATES` | `rc_driver.py` + `rc_probe.c`: 60 public bzlib cases (streaming with 1 B..64 KiB legal short chunks, buffer API, stdio API, truncated/bit-flipped/bad-magic streams, parameter validation, BZ_OUTBUFF_FULL, `/dev/full` write with errno ENOSPC), 2 fresh-process observations per arm |
| repeat_in_process | `GATES` | same cases twice in one persistent process per arm vs the fresh oracle; per-call fds/threads (tid, start_ticks, state, flags)/children |
| aliasing | `GATES` | reviewed library-owned pointer surface: `BZ2_bzReadGetUnused` (pointer into the BZFILE buffer, valid until `BZ2_bzReadClose`) checked against the file bytes after the consumed position, copied before close and chained into the next `BZ2_bzReadOpen` (multi-stream, trailing garbage); `BZ2_bzerror` constant strings. `bz_stream` and all buffers are caller-owned; no other accessor exists |
| cli_faults | `CLI_CASES` | upstream `source/bzip2` (declared public interface): small stdout `-c -9`, large stdout `-dc`, file-output `-k` with read-only directory fault |
| header_consumers | `HEADER_CONSUMERS` | `header-manifest.json`: real two-TU C and C++ consumers of `source/bzlib.h`, linked to the matching build's `source/libbz2.a`. Compiler-default dialects are bound to the pinned Makefile flags and the public `#ifdef __cplusplus extern "C"` guard; no numeric C++ portability minimum is inferred. The native regression test additionally executes both linked programs' round-trip and error/lifecycle calls. |

### Toolchain rollout

The older task image lacks a C++ compiler. Its retained pending C++ receipts remain
valid evidence of that old image, not passing evidence for this change. The revised
Dockerfile adds `g++` and records its resolved package version in
`/app/os-packages.lock`. Rebuild **both the task image and the matching agent
environment**, pin the resulting immutable image digest, restage the reviewed
verifier and run fresh qualification before scoring. Do not install a compiler
into an already-qualified image or reuse its qualification marker.

The source version/seed, workload definitions, sampling policy, frozen release gate
policy and expected output commitments are unchanged. This is an image/toolchain
revision, not a backfilled qualification or a change to the frozen policy.

Native controls (public source only):

```sh
BZIP2_HEADER_SOURCE=/path/to/authenticated/bzip2-1.0.8 \
  python3 -m unittest discover -s tasks/bzip2-codec/tests/fuzz -p 'test_header_native.py' -v
```

Every source file is checked against `pristine-files.lock.json` before building.
The tests require real two-TU C/C++ compile/link success and successful execution,
reject a native C++ linkage mutant, and keep a missing C++ compiler as an
infrastructure failure. Set `BZIP2_HEADER_RECEIPTS` to a new directory to retain
the raw commands, tool identities, observations and execution results. This
calibration is not a machine qualification or an optimized-candidate score.

Every library observation carries `diagnostics.execution_affinity` (sched_getaffinity at call time).

Error policy. Code, name, errno and operation are compared exactly. The only stage
normalization is `write`/`write_close` → `write_sequence` for the stdio write sequence on one
BZFILE, declared in the surface's `normalizations` with source authority (bzip2 manual 3.4.6
"Absorbs len bytes ... eventually to be compressed and written to the file"; 3.4.7
"Compresses and flushes ... all data so far supplied by BZ2_bzWrite"). The raw stage and the
number of `BZ2_bzWrite` calls accepted before the first error are retained in diagnostics and
not compared. No call is made on a BZFILE after its first error except the documented
`BZ2_bzWriteClose(abandon=1)` cleanup. Pristine bzip2 reports directory and write-only readers
as `BZ_UNEXPECTED_EOF` (no errno); recorded as observed. Partial decompressor output before
an error is compared only as a prefix of the original. Compressed fixtures come from Python's
independent `bz2` module, never from the candidate.

Not exercised: `bzopen/bzdopen` zlib-compatibility wrappers, custom allocators, injected BZ_MEM_ERROR.
Comparator: the central exact typed contract comparator (`COMPARE` is not overridden); CLI
uses the default exact-bytes output comparator.
