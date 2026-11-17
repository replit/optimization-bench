# XZ public memusage semantic oracle

This verifier-only probe checks **reported workspace against actual owned
allocation requests**, independent of report-only peak RSS ratios. It never
caps candidate memory, compares candidate/pristine memory ratios or requires
their estimate return values to be exactly equal. A valid larger estimate and
a valid larger allocation both pass when the public estimate accounts for the
owned workspace. Final task verdict authority belongs to the shared controller.

## Contract and scope

Pinned XZ source: `299eb5c3f597f107fd6019bf156885ac8bfaa5ea`, archive SHA256
`c1f497513e85cbc2f3760990a6ed9cbebe6386f273481b87251d0d6eb481c51a`
from `environment/source.lock.json`. The standalone replay also verifies the
entire extracted source inventory (including file modes), not just the two
mutated files. It refuses arbitrary candidate/sweep sources.

Policy `xz-requested-owned-workspace-upper-bound-v1` implements the approved
design §5 workspace consistency check. Public header references:

- `src/liblzma/api/lzma/filter.h`: `lzma_raw_encoder_memusage` and
  `lzma_raw_decoder_memusage` return approximate required memory for a filter
  chain; also usable for Block/Stream coders with small extra overhead.
- `src/liblzma/api/lzma/container.h`: easy preset wrappers and
  `lzma_stream_encoder_mt_memusage`.
- `src/liblzma/api/lzma/base.h`: `lzma_memusage` is supported only by
  initializers accepting a memlimit. It describes the **current decoder filter
  workspace**, not the historical peak, and must not be queried on raw/encoder
  streams as if it were supported.

The headers say **approximate**, not exact allocation counts. The pinned
implementation deliberately adds a conservative `LZMA_MEMUSAGE_BASE` allowance
in `common/filter_common.c`; the MT formula separately includes buffers/queues
and workers in `common/stream_encoder_mt.c`. We preserve overestimation and use
the approved owned-workspace upper-bound policy, with **no invented numeric
tolerance** and no comparison to pristine's exact report. The integrator/task
owner must review this source-backed policy for any newly added filter/API
whose documented approximation/exclusions differ; this seven-mode reference
is not authority to invent a bound for an unrelated API.

`probe.c` emits `xz-allocation-ledger-v1` JSONL. A mutex-protected custom
`lzma_allocator` records allocation IDs, requested payload sizes, frees, live
bytes, lifetime peak, outstanding counts and per-call observations, including
MT worker callbacks. The controller reconstructs aggregate counts from events
rather than trusting reported sums. Each mode exercises full completion, early
end, and reinitialization of the same public stream object. Caller input/output
cursors are explicitly reset after early end; stale input is not fed into a
new stream.

| Mode | Public estimate/report |
|---|---|
| raw-encode | `lzma_raw_encoder_memusage` |
| stream-encode | `lzma_raw_encoder_memusage` for the same filter chain |
| easy-encode | `lzma_easy_encoder_memusage` |
| mt-encode | `lzma_stream_encoder_mt_memusage` for identical thread/block/filter/check options |
| raw-decode | `lzma_raw_decoder_memusage` |
| easy-decode | `lzma_easy_decoder_memusage` for a frame encoded with that preset |
| stream-decode | `lzma_memusage` on the current decoder stream |

Static estimates bound the observed owned payload peak across a lifecycle;
`lzma_memusage` is checked against **current** live payload at each call, not
allocations already freed. Zero and UINT64_MAX are not usable reports for these
valid cases. End/early-end requires zero outstanding allocations. Public API
error returns on the valid options are recorded as typed observations and
diagnosed separately, not confused with a failed compiler or malformed probe.
The probe reporter uses C stdio, not the liblzma function under test.

### Explicit exclusions and remaining instrumentation gap

Count all payload bytes requested through the supplied public allocator,
including liblzma container bookkeeping and internal buffers. Exclude caller
input/output storage, pre-encoding preparation, verifier ledger nodes and
allocator implementation metadata. The callbacks cannot observe:

- Heap allocations that bypass the documented custom allocator.
- Thread stacks, TLS, OS pthread metadata, static/global or caller stack arrays.
- Memory safety or races by themselves.

Those are **not blanket exemptions for a candidate**. The XZ task owner must
audit/instrument new allocation paths and supply independent allocator-hook
coverage, native sanitizers/race/lifecycle evidence. A candidate bypassing the
public allocator cannot be called fully qualified from this ledger alone.
No unsafe malloc interposition is used. Per-unit cgroup peak reporting is a
separate controller responsibility and remains reporting-only.

## Compile and replay each arm independently

Inside each pinned task worker, after its normal library build:

```sh
cc -O2 -g -Wall -Wextra -Werror \
  -I/arm/project/src/liblzma/api \
  /verifier/memusage/probe.c /arm/cmake/liblzma.a -lpthread \
  -o /scratch/memusage-probe
/scratch/memusage-probe mt-encode 0 2 131073 4096 3072 > /scratch/ledger.jsonl
python3 /verifier/memusage/oracle.py /scratch/ledger.jsonl
```

Arguments: mode, preset (0–6), threads (1–8), deterministic public input size
(0–2 MiB), input chunk and output chunk (1–2 MiB). Corpus generation here uses
fixed public synthetic bytes (xorshift seed 1729), not held-out files. Decoding
preparation occurs outside the measured allocator lifecycle; the XZ adapter's
independent byte/output oracle still handles codec correctness. This narrow
probe does not replace it, a full surface/filter/options matrix, interleaved
instances, fault schedules, race builds or CPU-affinity repetitions.

Never load baseline and candidate in one address space. `oracle.read_result`
validates each arm's transcript; `oracle.compare` refuses candidate attribution
if pristine does not pass. Malformed/truncated transport, wrong API/schedule,
aggregate inconsistency and probe preparation failures are `invalid_run`.
The result retains worker exit status: the **task controller** must separately
diagnose/replay abnormal candidate worker termination, not silently discard a
candidate crash because no complete allocator ledger arrived. This helper's
transport diagnosis is not a final candidate verdict.

## Mandatory real source controls and local evidence

`controls.py` supplies pinned, unique-anchor source edits:

1. Halve `lzma_raw_encoder_memusage` while leaving allocations untouched.
2. Enlarge the existing history allocation by 2 MiB without changing the
   estimator; keep the same valid buffer use and normal free path. This catches
   hidden owned workspace, not merely leaks or a mutated verifier.

Neither control changes the probe, comparison oracle or candidate source.

```sh
python3 tasks/xz-lzma2-compress/tests/fuzz/memusage/validate.py \
  --source /scratch/verified-extracted-pristine-xz \
  --out /scratch/new-memusage-validation
```

The reference uses the task's `-O2 -g -DNDEBUG` library flags, separate fresh
processes, and a local GCC/CMake/Ninja build. `evidence/validation.json` records
**84 passing pristine trials** (seven modes × input sizes 0, 1, 65535, 65536,
65537, 131073 × two replays). Both compiled source controls fail original and
minimized cases twice. Their smallest valid input is **empty**, preserving
surface/options/lifecycle/failure category. Six minimized raw ledgers
(pristine + two mutants, two replays each), plus the corresponding six original
raw ledgers, are retained with the compact receipt; remaining matrix transcript hashes are reproducible by the
script. Each receipt binds source/build/compiler/probe/oracle identities,
options, complete failure findings and allocation observations.

This is a **local control demonstration**, not fresh pinned-container
qualification or a >=20-mutant task score. `qualification: undertested` is
intentional. The XZ task owner wires the probe into independent task workers,
adds the rest of its portfolio and campaigns, and reports all remaining gaps.
