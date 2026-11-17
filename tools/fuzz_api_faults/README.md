# Generic public API faults and mutable-factory state

These verifier-owned probes implement generic **classes** of correctness
checks, not candidate-specific source patterns. No candidate hash selects a
probe. The task-policy registry binds documented APIs, legal argument domains,
mutators, observations and explicit applicability gaps. All code stays outside
task environments and production candidate sources. There is no memory cap.
The shared judge owns admission, error completeness, resource policy and final
verdicts; these drivers return **facts**, never self-awarded task passes.

## Long-lived sequence protocol

```sh
python -m tools.fuzz_api_faults.worker --runtime /arm/build/runtime
```

The controller runs one isolated process/library module per arm, sends JSONL
requests, and collects independent baseline/candidate facts. Modules are loaded
once and never reloaded between requests or repetitions. A request is:

```json
{
  "format": "judge-generic-api-sequence-v1",
  "task": "sqlfluff-project-lint",
  "adapter_id": "sqlfluff.mutable-block-config.v1",
  "request_id": "mutable-isolation",
  "cases": [
    {"case_id": "A", "surface_id": "sqlfluff.utils.reflow.config.ReflowConfig.get_block_config",
     "operations": [], "faults": {}}
  ],
  "repetitions": 3,
  "order": ["A", "A", "A"]
}
```

The displayed empty operations array is a schema illustration, **not an
admitted case**. Generate the complete bounded A/B/A requests:

```python
from tools.fuzz_api_faults.factories import sequence_request
mutable = sequence_request()
frozen = sequence_request(frozen=True)
```

Ready examples are `examples/mutable-request.json` and
`examples/frozen-request.json`. Each contains two valid variants and A/B/A
order, repeated three times. All case/operation preconditions are checked
**before target import or invocation**. Unknown adapters, methods, malformed
arguments and frozen/private nested mutations are rejected as out of contract.
Import/driver/transport errors remain invalid runs, not candidate differences.

Responses have `format: judge-generic-api-sequence-result-v1`, request/task/
adapter identities and `sequence_results`, with case/surface, repetition,
order index and per-call records. Each record contains the generic
`optimbench-fuzz-observation-v1` shape: termination, typed outcome, effects and
diagnostics, plus operation/step metadata. Python errors retain nonempty
module-qualified `class` and exact `message`; the shared surface policy
specifies which fields/messages are contractual. The **trusted controller**
adds/authenticates source/build/case/worker identity; the target does not
self-assert those hashes. No aggregate error dictionary is allowed to hide a
missing native category/code or Python class/message.

The observer writes JSON to a private duplicated report descriptor. Target
fd1/fd2 are redirected to verifier-owned temporary files, and per-call complete
bounded bytes appear as `effects.target_stdout_hex`/`target_stderr_hex`.
Ordinary print or native `os.write(1, ...)` cannot corrupt the report channel.
More than 1 MiB output in one call is unavailable/invalid transport, never a
truncated success. Unexpected worker death is handled/replayed by the shared
controller, not misclassified from a missing response.

`semantic()` removes only per-call diagnostic resource identities. It does
**not** erase error messages, typed distinctions, public values or target
output. The shared `fuzzing_contract` comparator/CLI stderr policy remains the
authority; no new Python bool/int coercion or blanket error normalization is
introduced here.

## OS-error stderr facts

`tools.fuzz_api_faults.os_errors.parse_os_error(stderr)` returns a facts
dictionary or `None` for unsupported/ambiguous text. Its fields are
`exception_class`, `errno` (numeric or null), `code` (symbolic or null),
`operation_category`, `path`, `destination_path`, `internal_temp_frame`, and
`rawmessage` (the **entire unchanged stderr**, including whitespace).
It recognizes builtin Python OSError-family terminal exceptions with an
explicit `[Errno N]` marker and a conservative set of Node `Error: ECODE:`
headers, optionally followed by stack frames. Node object dumps, chained or
multiple OS reports, and non-OS exceptions remain unclassified. Unknown
operations are `unreported`; numeric and symbolic errors are not translated
using the observer host's OS.

Paths are decoded only, never normalized or equated. `internal_temp_frame`
requires the same Python traceback to contain `_mkstemp_inner` in a stdlib
`lib/pythonX.Y/tempfile.py` (or Windows `Lib/tempfile.py`) implementation
frame. A `/tmp` path, random filename, user `tempfile.py`, or missing frame
is **not** enough. This flag is only frame provenance, not a path-role
assignment or permission to ignore a path. The shared controller owns declared
input/output path roles, OS-error identity semantics, and verdicts. The parser
is not automatically wired into any judge or comparator.

Run focused checks with:

```sh
python3 -m unittest tools.tests.test_fuzz_api_faults_os_errors -v
```

## Documented mutable factory vs frozen return

`registry.json` supplies two initial Python adapters:

- `ReflowConfig.from_dict/get_block_config` and `BlockConfig.incorporate`.
  Construct a documented layout mapping, get twice, observe the second,
  legally incorporate into the first, then observe the second and a third
  getter result. Interleave a second independent factory and repeat A/B/A
  with varied valid spacing. Compare **public fields**, never `id()` or private
  caches. This catches shared mutable factory returns even when ordinary
  single-result formatting does not.
- `MatchResult.empty_at/append` on valid empty matches. Read public slice,
  length and truth values; repeat/interleave documented operations. No nested
  `segment_kwargs` mutation, arbitrary AST or object identity comparison.
  Safe interning of a frozen result is a mandatory **must-not-flag** control.

Extending to another factory requires a source-backed registry adapter with
constructors, valid argument families, **documented mutators** and public value
observation. A general cache/singleton is not automatically incorrect, and a
shallow frozen dataclass does not authorize inventing nested dictionary
mutations that its API never documents.

## Legal reader/writer fault schedules

`faults.rs` is a dependency-free Rust `std::io::Read/Write` wrapper module;
`streams.py` supplies equivalent binary Python interfaces. These are reusable
by verifier drivers for public stream APIs (serde_json and Zrip where bound),
not arbitrary private functions. The target library must not parse the fault
schedule or serialize its own observation report.

`FaultPlan` fields:

| Field | Meaning |
|---|---|
| `max_chunk` | Positive bound on accepted bytes per nonempty call |
| `fail_after` | Accept at most N bytes, then error |
| `zero_after` | Write returns zero / read returns EOF after N bytes |
| `eof_after` | Reader-only explicit EOF boundary |
| `interrupt_at` | **One-shot** EINTR at the logical accepted-byte boundary |
| `error_kind` | Other, BrokenPipe, PermissionDenied, UnexpectedEof |
| `flush_error` | Writer error only if flush is actually invoked |

Terminal error/zero/EOF schedules are mutually exclusive. Cross-boundary
requests first return `Ok(n)`/a short read for the accepted prefix; a subsequent
error accepts **zero** bytes. One-shot EINTR splits at the boundary and is not
consumed by empty-buffer calls. Permanent `Interrupted` error schedules are
rejected. Fault positions depend on logical accepted bytes, not the target's
write-call partition. `write_all`/`read_to_end` retry semantics belong to their
documented language API; individual library functions may propagate errors
instead. The oracle compares the documented public outcome, not an invented
universal retry requirement.

Rust wrappers expose `FaultWriter::new(plan)`, `.bytes`, `.schedule` and
`FaultReader::new(data, plan)`, `.schedule`. Schedule exposes position, one-shot
state, call/error counts and up to 256 diagnostic `(requested, accepted)`
pairs. Written/accepted bytes and typed public errors are semantic; call
partition, exact syscall count and unpromised reader read-ahead are diagnostic.
Never assert that every serialization operation must implicitly flush.
Python verifier teardown suppresses only its own destructor flush; explicit
library flush/close operations still exercise the configured fault.

Native drivers can include:

```rust
#[path = "/verifier/fuzz_api_faults/faults.rs"]
mod faults;
#[path = "/verifier/fuzz_api_faults/proc.rs"]
mod proc;
// Before EACH public library API call:
let before = proc::capture(); // Err means unavailable, NOT empty counts.
// ... call library; catch/record return/error/panic independently ...
let after = proc::capture();
```

The registry explicitly records that XZ's `lzma_code` has caller buffer
semantics, **not** Rust/Python I/O hooks. Native chunk/truncation/reset cases
must use its actual API; its public memusage check remains separate. Msgpackr
stream applicability is pending a pinned public export/contract authority,
not silently waived or fabricated. No Node partial callback write semantics
are invented from Rust's `Write`.

## Per-call lifecycle facts

### Live identity schema v2

Current snapshots use `schema: per-call-live-identities-v2`.
`thread_identities` contains only observed live records with `tid`,
**kernel `start_ticks`**, `state`, numeric kernel `flags` (proc stat field 9),
optional `tgid`, and proc-view `scope_pid`.
`threads` and `counts.threads` are derived from that **same list**, never a
second aggregate source. Dead `Z`/`X`/`x` or genuinely vanished tasks are
excluded using observed state, not thread names or runtime whitelists. Linux
`PF_EXITING` (`flags & 0x4`) tasks are also excluded even if still in state
`R`: kernel exit can leave a task visible after `pthread_join` completes.
Both initial and confirming stat reads check this flag. Their exact
TID/start-time/state/flags/scope identities remain in
`excluded_exiting_threads`, which is diagnostic witness data, **not** live
worker counts or a complete exclusion history. No sleep, name, or count
allowance is used; live sleeping, detached, and blocked tasks remain visible.
A visible task without readable stat/start time is unavailable/pending, not an
invented zero and not a proven leak.

Python snapshots include before/after `phase`, a `time.monotonic_ns` sample
interval, and raw/thread/child method names. `validate_snapshot()` returns
pending for missing identities/flags and raises for identity/count/clock
mismatch or an exiting task included in the live list.
The controller must reject inconsistent counts as invalid evidence. Additional
live lifecycle facts remain `needs_review` until semantic ownership is shown;
this sampler grants no approval path or automatic worker exemption.

Rust `capture_phase("before")` / `capture_phase("after")` records a process-local
`std::time::Instant` interval with explicit clock method/scope. The compatible
`capture()` uses phase `"sample"`. `thread_identities` and `threads_count` are
optional: `None` is pending. The old native `threads: Vec<u32>` is retained
**only for source compatibility**, derived from those same live identities.
Native JSON **must emit `threads_count` as `threads`**, not vector length when
the optional measurement is missing. `validate_thread_counts()` rejects a
count/list mismatch; raw TIDs alone do not meet the v2 contract.

FD link targets are SHA256-only (`target: "sha256:..."`, `target_sha256`), never
raw file paths. The native std-only SHA256 helper is included as `path_hash.rs`
and tested against published empty/abc/million-byte vectors. Mount/copy that
file with `proc.rs`; no compiler/library dependency is added to candidates.
No cmdlines, environment contents or thread names are retained.

The previous v1 reference artifacts remain unchanged for provenance. New v2
source-bound control replays and concrete identities are retained separately
under `evidence/thread-identities-v2/`; old aggregate-only facts cannot be
silently promoted to a complete v2 measurement. The retained v2 SQLFluff
captures also predate kernel flag collection: their hashes remain unchanged,
but they are now explicitly pending `thread_flags_unavailable` under the
current validator. They cannot retroactively prove that an old extra task was
`PF_EXITING`, and they do not qualify the updated sampler.

Python `lifecycle.snapshot()/observe()` and native `proc::capture()` record
fd numbers/targets, thread IDs, recursive child PIDs with process start ticks,
and explicit measurement availability. Observations occur around every public
operation, including constructors/getters/mutators, within the same process.
Known observer descriptors are part of the same overhead in both arms.
If kernel task `children` files are absent, both implementations fall back to
scanning only `/proc/<pid>/stat` PPID/start-time/state inside the controller's
private PID namespace. They do not read cmdlines or environments. The sampler
records OS PID and proc-view PID separately, preventing namespace translation
from being mistaken for zero children. If fallback is unavailable, Python
retains known fd/thread facts with `children: null` and availability false;
Rust returns `children: None` plus an error/method while retaining fd/threads.
Neither turns an unsupported counter into an empty successful measurement.

Joined transient workers are not surviving workers. Resource identities are
diagnostic, never compared numerically across different baseline/candidate
PIDs. The shared policy evaluates lifecycle deltas and documented runtime
ownership; this module does not invent a universal one-thread rule or waive
the existing unconditional-fork `process_state` hard block. Snapshots do not
prove absence of a transient race; TSan/Go-race/deeper campaigns remain separate.

## Local reference evidence and replay

```sh
python3 -m unittest tools.tests.test_fuzz_api_faults -v
python3 -m tools.fuzz_api_faults.validate_sqlfluff \
  --source /scratch/hash-verified-pinned-sqlfluff \
  --python /scratch/task-python \
  --out /scratch/new-factory-control-run
```

The proc monitor's native unit binary must run with `--test-threads=1`
(the Python launcher supplies it). Its assertions inspect process-wide
resources; parallel libtest workers starting/exiting are not the owned
thread/child under test and can make a snapshot legitimately pending.
For standalone replay:

```sh
rustc --edition=2021 --test tools/fuzz_api_faults/proc.rs -o /scratch/proc-tests
/scratch/proc-tests --test-threads=1
```

This isolates only the unit harness. Production monitor state filtering,
identity availability, count/list validation and lifecycle policy are unchanged.

The source archive hash is the task's unchanged pin; exact factory files are
bound in `sqlfluff-source-pins.json`. The validation script creates only
disposable pristine copies: one control unsafely caches a mutable factory
return, and one safely interns `MatchResult.empty_at`. Neither is candidate-
specific detection logic. Each arm uses two fresh processes, each process
executes both adapters in the preferred long-lived repeated A/B/A protocol,
and every public step retains lifecycle facts.

The reference establishes detection of mutable aliasing and **no flag** for
safe frozen interning. Raw requests/semantic observations/per-call lifecycle
facts are retained under `evidence/`, with source/driver hashes. This local
Python runtime reference is **not** a full pinned-image candidate campaign,
public surface completeness, sanitizer/race claim or >=20 mutation score.
Those deep campaigns continue independently; they do not prevent delivery of
the tonight RC generic gates. The shared controller/task owners wire and
replay actual archived candidates; no historical receipts are rewritten here.
