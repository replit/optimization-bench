# SQLite RC registration (judge-v1.1-rc-generic)

Hooks return facts; only the shared controller assigns verdicts. This registration
does not certify full hardening, mutation adequacy, timing, or image qualification.

| Gate | Registration | Evidence source |
|---|---|---|
| stream_faults | `GATES` | 116 public SQL/VFS cases, two fresh observations per arm. Facets, FTS5 bm25, transactional UPSERT and VACUUM; default Unix VFS wrapped using `sqlite3_vfs_register`. Zero-filled short reads, read EIO, write SQLITE_FULL, sync/truncate errors; mmap off/on and legal xFetch success-with-no-page fallback. Public Unix VFS xSetSystemCall additionally injects actual pread/pread64 EIO/EOF below os_unix.c, so swallowing Unix read errors is observable. |
| repeat_in_process | `GATES` | Entire corpus twice in one native linked-library process, varied seed A/B/A across freshly created connections/databases, compared with fresh workers. Every call has actual fd/thread/child snapshots; thread tid, start_ticks, state and scope_pid retained. |
| aliasing | `GATES` | sqlite3_column_text/blob copied within valid lifetimes, reset/clear_bindings/rebind, type conversion with pointer reacquisition, sqlite3_value_dup surviving statement finalization, sqlite3_errmsg consumed before the next SQLite call. Never dereference stale pointers. |
| cli_faults | `CLI_CASES` | Real upstream objects/sqlite3 shell: small grouped output, large recursive query output, `.once out/result.txt`. Readonly immutable input database and SQL watched in every case. Shared runner injects real stdout and readonly-bind faults. |
| header_consumers | `HEADER_CONSUMERS` | Two distinct C89 TUs compiled separately for sqlite3.h and sqlite3ext.h, linked against matching objects/libsqlite3.a. Extension consumer uses the documented SQLITE_CORE static-extension mode. Pinned source AGENTS.md C89/C99 convention plus public header declarations bind scope. |

Native observer compiles against each matching build's generated headers and static
library, not the host Python SQLite. Python SQLite only creates the independent
small CLI seed database. Library cases are explicit verifier-generated public SQL,
not substituted production workload data. Every SQL result retains column types,
complete values (double values as exact hexadecimal), ordered rows, primary and
extended return codes, and complete sqlite3_errmsg. Recovery retains autocommit,
explicit rollback result, integrity_check and ordered contents, and close result.
No error-message normalization or candidate-specific branches are used.
Both same-connection and reopened integrity observations are retained: a failed
FTS5 virtual-table constructor can poison that connection's schema view while the
reopened database remains intact. This pristine limitation is not erased.

Faults start at data pages (offset >=4096) rather than manufacturing an invalid
database header. Read-fault mmap cases decline xFetch pages to exercise fallback;
healthy mmap cases exercise the real mapping. Syscall overrides exist only inside
the verifier worker. Zero-length short reads are valid EOF fault analogues; this
is not a claim to cover all partial-read/EINTR schedules. Fault hit/read/fetch
counts remain diagnostics (not frozen optimizer-specific I/O counts).

`/proc/<pid>/task/<tid>/children` is preferred; kernels lacking that file use
actual proc-stat PPid enumeration, not invented zero children. Counts derive from
the same concrete thread list. Failure to read required lifecycle data is an
infrastructure error. The parent samples at native command boundaries; it never
spawns a fresh target for a persistent call.

Local validation (no Docker or performance measurements):

```sh
python3 tasks/sqlite-distinct-plant-facets/tests/fuzz/rc_driver.py \
  --baseline /path/base --candidate /path/base \
  --baseline-sha "$BASELINE_BUILD_SHA256" --candidate-sha "$CANDIDATE_BUILD_SHA256" \
  --out /path/fresh-facts
SQLITE_RC_BUILD=/path/base SQLITE_RC_FACTS=/path/fresh-facts \
  python3 -m pytest tasks/sqlite-distinct-plant-facets/tests/fuzz
python3 -m judge.fuzzing_inventory --out docs/fuzzing-rc-registration-inventory.json
```

Build source comes from environment/source.lock.json, verified against both its
archive digest and pristine-files.lock.json. Configure/build flags are exactly the
task recipe's static/FTS5/RTree flags. Testfixture requires tcl-dev, already present
in the task image. No image or judge policy changes are required for these C cells.
C++ consumers and dynamically loaded extension ABI are outside this C registration;
no unsupported/N/A claim is made for them. Additional scope review remains separate.

On an isolated judge VM, stage authentic candidate artifacts and pin the existing
task image, then use a fresh output directory:

```sh
python3 -m judge.score --task sqlite-distinct-plant-facets \
  --artifacts /path/to/artifacts --mode provisional --out /path/to/fresh-result \
  --gates-only --gate-profile v1.1-rc --image 'repository@sha256:IMAGE_DIGEST' \
  --cpuset 0-7 --cache /path/to/cache --separate-sandbox
```

Only use separate-sandbox with actual isolation attestation. score.py runs
cpu_correctness.check only after generic gates pass; it replays registered cases
on 1/2/4/8 CPUs and emits the missing CPU-subset receipt. Native success is not
that receipt. Shared CLI fault mounts, real-image header compilation, CPU replay,
existing behavioral/process-state admission and qualification still require the VM.

## Native must-reject calibration

Local uncommitted source-tree controls were configured and built with the same
static/FTS5/RTree flags, observed with this driver and classified with
`judge.fuzzing_generic._call_evidence` (not a task-local comparator).

| Pair | Stream | Repeat | Alias |
|---|---|---|---|
| Pristine / pristine | passed | passed | passed |
| Unix read-error/short-read swallowing control | contract_regression | contract_regression | passed |
| Cross-connection aggregate static-cache control | passed | contract_regression | passed |

The read control changes os_unix.c to retry a failed/short read directly through
libc pread, bypass the registered syscall and return success. Both EIO and EOF
cases change from pristine SQLITE_CORRUPT (11) to success (0); mmap=1 records an
actual xFetch fallback, not just an enabled pragma. The cache control changes
vdbeapi.c to memoize a facet minimum per family across connections. It preserves
every fresh-worker result but changes persistent seed-B results after seed A.
Mutants, builds and raw calibration output are local-only, not production inputs.
