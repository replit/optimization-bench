# liblzma differential verifier

Enabled, verifier side only; no network or agent-visible files. `generate.py` emits
12 deterministic bundles (24 variants) per seed. `run.py` compiles `probe.c`
against the mounted candidate's declared public headers and static library.
The immutable image's pristine library supplies only the exact sizes used to
choose buffer capacities; it never supplies candidate-dependent inputs.

## Coverage and comparison

- Frozen, manifest-addressed public source files: seeded fragments, cross-file
  recombination, reversal, high-bit mutation, NUL/binary suffixes; 0/1/31/1024/8192
  byte boundaries before mutation.
- `lzma_easy_buffer_encode` (including the scored preset-6/CRC64 API) and
  `lzma_stream_buffer_encode`; capacities 0, 1 and pristine encoded size minus
  one, exact, plus one, for both encoders. Compare status, output position,
  complete emitted bytes and the caller's initialized capacity on failures.
- Single-threaded stream encoder and native MT encoder (one/two threads);
  presets 0/1/6/9, extreme, checks NONE/CRC32/CRC64/SHA256, no prefilter,
  delta, x86 and ARM64, and invalid lc/lp or MT thread options. This is a bounded
  cross-cover, not an exhaustive Cartesian product of liblzma's numeric options.
- Streaming input chunks 1/7/4096, output chunks 1/17/4096; complete per-call
  statuses, consumption counters and output bytes. Two independent streams are
  advanced round-robin with different encoder payloads, ended and reinitialized.
  MT uses blocking FINISH, whole input and ample output so worker timing is not
  mistaken for a semantic result.
- Public stream decoder: valid, truncated, header-corrupt, non-XZ-prefixed,
  concatenated and empty streams. Python's image-pinned system liblzma prepares
  the compressed fixtures identically for pristine and candidate.
- Exact outer process exit status, stdout and stderr (lossless hex), plus exact
  native transcript. No hash-only, ratio, roundtrip-only, or prefix comparisons.
  Native library failures are observations, not harness failures.

This exercises the listed public codec APIs, not every liblzma API/filter/flush
action. It does not modify builds, timed workload, source, frozen inputs, judge,
or the shared runner. Tests are mounted read-only only in the verifier.

## Actual Docker validation

Pinned task image built unchanged from `environment/Dockerfile`:
`sha256:461eca58c90a67a5aebc2a2422316b01a2bd996e284364eb40aa7cf26f24774c`
(local tag `diff-xz`); upstream source commit remains
`299eb5c3f597f107fd6019bf156885ac8bfaa5ea`.

- Seeds **1729, 2718, 31415**: unchanged baseline versus unchanged baseline,
  **36 bundles / 72 variants**, zero mismatches, **92.244622 s total**.
- Negative library control, seed **1729**: in a scratch source copy only, insert
  `if (check == LZMA_CHECK_CRC64) check = LZMA_CHECK_CRC32;` at entry to
  `lzma_stream_buffer_encode` in `src/liblzma/common/stream_buffer_encoder.c`.
  Rebuild the scratch library with the task image's CMake/compiler. Caught bundle
  **8**, **45.373368 s including deterministic shrinking**.
- Shrunk from 1024-byte input/two variants to one **empty-input** preset-6/CRC64
  buffer case (`regression.json`). Two independent replays through
  `judge.differential.execute` reproduced both complete saved observations and
  their mismatch exactly, **3.051605 s total**.
- Runtime cap: 600 s total, 30 s per case. All qualified cases finished within
  the configured limits. Tests use read-only builds, UID 10000, no network,
  dropped capabilities, and the actual `judge.differential.check` Docker path.

Hash-locked binary-only interface requirements installed in a venv.
`tools/check_tasks.py`: 21 tasks; judge tests: 117 passed, 12 skipped;
tools tests: 56 passed, 4 skipped. No unsupported skipped checks counted
as library qualification.

Reproduce the pristine control (export/copy `/app/baseline-build` from the image
to a host build directory first):

```sh
python -m judge.differential --task tasks/xz-lzma2-compress \
  --baseline /path/to/baseline --candidate /path/to/baseline \
  --image diff-xz --cpuset 0-7 --output /tmp/xz-control
```

For the negative control, create a fresh CMake build directory in the scratch
copy, build the changed library, and replace only that copy's `cmake/liblzma.a`.
Use its build directory as `--candidate`, adding `--seed 1729`. Replay the
`case` in the emitted reproducer (or `regression.json`) with
`judge.differential.execute` against both builds; do not add it to agent inputs.
