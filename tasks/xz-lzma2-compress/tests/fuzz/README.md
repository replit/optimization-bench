# Native correctness controls

The shared judge owns every verdict. `rc_hooks.py` only registers raw native
observations and public CLI/header requests. These are additional protected
correctness checks; they do not replace either timed workload, its drivers,
upstream tests, frozen inputs, CRC rejection, or concatenated-stream cases.

- `rc_probe.c` links the actual arm's static liblzma. A persistent native process
  executes each of 12 bounded cases twice, interleaved with the other cases.
  Each case also executes twice in independent fresh processes.
- Stream cases preserve exact returned statuses, input/output cursor traces and
  every output byte. They cover one-byte buffers, truncated input, corrupted
  checks, concatenated streams, zero output capacity followed by recovery,
  allocator failure, and reinitialization of an unfinished stream.
- Ownership cases initialize two encoders over the same immutable caller input,
  with separate outputs; the first output stays retained while the second
  encoder runs. Arbitrary overlapping input/output compression buffers are not
  claimed to be a supported upstream API contract.
- Per-call observations read the real native PID's descriptors, children, and
  concrete OS thread identities before and after each call. Actual native
  `sched_getaffinity` observations accompany each result.
- The real `xz` binary runs small/large stdout and file-output cases through
  both arms and replays. The shared harness injects `/dev/full`, closed reader,
  closed stdout and read-only-directory faults, retaining raw outputs and
  filesystem effects. File-output credit requires a real healthy write.
- Two independently compiled and linked consumers cover the entire public
  header inventory through legal `lzma.h` inclusion, in C89 and C++98 modes.
  The pinned upstream explicitly promises C89/C++ API-header compatibility;
  source hashes and literal authority quotes are frozen in the manifest.

Run the focused shape/domain tests from the repository root:

```sh
PYTHONPATH=.:tools python3 -m unittest discover \
  -s tasks/xz-lzma2-compress/tests/fuzz -p 'test_*.py'
```

For real image-pristine component execution (Docker, host `binutils`, a working
C library for the extracted binaries, and the locked host Python dependencies
are required):

```sh
docker build -t optimbench:xz-lzma2-compress tasks/xz-lzma2-compress/environment
PYTHONPATH=.:tools:tools/runner python3 tasks/xz-lzma2-compress/tests/fuzz/native_smoke.py \
  --image optimbench:xz-lzma2-compress --out /tmp/xz-native-smoke
```

Run the component command from a trusted root evaluator shell with Docker
access. The shared CLI observer emits owner-only UID-10000 receipts; the host
must be able to read those and clean up container-owned build files. This does
not change container credentials or hardening. CI archives the original
evidence before upload without changing the tested files' ownership or modes.

Use a fresh output directory. This smoke run performs no timing or model calls.
It is deliberately not the candidate-export/reconstruction or official
eight-CPU scorer route. Follow `docs/judge.md` for that separate route.
The existing `memusage/` API estimator/allocator-ledger oracles and their
compiled negative controls are retained, without memory-ratio gates.
