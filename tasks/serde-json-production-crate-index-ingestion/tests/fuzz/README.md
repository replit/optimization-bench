# Public native library controls

`rc_hooks.py` supplies actual native `stream_faults`, `repeat_in_process` and
`aliasing` observations to the shared judge. It does not decide competing
verdicts. The hardened DockerExecutor runs UID 10000 with read-only admitted
builds, no network and the requested CPU affinity.

The original 32 writer/reader/stream cases are unchanged. They cover eight
documented entrypoints, successful and short I/O, errors before/after partial
output, write-zero, EINTR, malformed JSON/UTF-8 and the custom Serialize
error-before-panic regression. Two additional ownership cases parse the same
input through overlapping immutable slices and borrowed map keys, clone the
owned value, mutate one owned result, then inspect all retained complete values,
borrowed entries and original input. The same 34 cases also run twice in one
persistent native worker, with fresh-process oracles and per-call thread,
file-descriptor and child-process observations.

The SJF1 input tape and typed result tape are verifier-owned. Report formatting
does not use the candidate parser or serializer. Comparisons preserve typed
values, complete accepted output, error category/kind/position and stream
offset. I/O partitioning, read-ahead and panic diagnostic text are diagnostics.
The protected workload's complete structured/exact-output comparator is
unchanged.

The library has no public CLI or C/C++ consumer headers. CLI non-applicability
is conditional on measured coverage of every declared writer/reader surface,
paired build-bound facts, a persistent worker and available lifecycle data.
Missing coverage remains pending, not passed. Header scope is bound to the
pinned source archive and pristine inventory.

## Focused tests

From the repository root:

```sh
python3 -m unittest discover -s tasks/serde-json-production-crate-index-ingestion/tests/fuzz -p 'test_*.py'
python3 -m unittest discover -s tasks/serde-json-production-crate-index-ingestion/tests/fuzz/realism -p 'test_*.py'
```

The ownership control builds a real disposable library mutant whose object
clone returns `Null`. Both pristine equality and reproduced mutant rejection
are assigned by `judge.fuzzing_contract`, using actual compiled libraries:

```sh
mkdir -p /tmp/serde-alias-controls
chmod 777 /tmp/serde-alias-controls
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 10000:10000 \
  --cpuset-cpus 0-7 --memory 16g --pids-limit 512 \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  -e PYTHONPATH=/repo:/repo/tools/runner \
  -v "$PWD:/repo:ro" -v /tmp/serde-alias-controls:/scratch \
  optimbench:serde-json-production-crate-index-ingestion \
  python3 /repo/tasks/serde-json-production-crate-index-ingestion/tests/fuzz/validate_aliasing.py \
  --out /scratch/result
```

`result` must not already exist. This is an untimed negative control, not an
optimization trial or performance claim. `validate.py --candidate BUILD`
provides additional bounded, seeded manual library replay. Neither focused
probe slice claims exhaustive API, feature, mutation or sanitizer coverage.
The separate upstream matrix, source/interface admission, complete public and
held-out outputs, process-state gate and mandatory 1/2/4/8-core replay remain
required by the shared judge.
