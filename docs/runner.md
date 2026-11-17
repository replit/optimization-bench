# Shared workload runner

## Process-exit clock

Non-resident steps observe exit using Linux `pidfd_open` plus event-driven poll,
then reap with a blocking wait. They do not timestamp after
`Popen.wait(timeout=...)`'s up-to-50-ms polling delay. Timeout still returns
`exit_code: null` and protocol `timeout`; post-exit cleanup (below) retains
freeze/kill ownership.
Resident ready/result stops and their measured boundary are unchanged.

Each step's telemetry records `exit_observation.method`. If pidfd is unavailable
or denied, the explicit `threaded-blocking-waitpid` fallback waits for an exit
event, not timeout polling, and records `fallback_reason`. The timeout waiter
returns without killing the child; the caller still performs post-exit cleanup.
Do not silently mix observation methods when comparing timing sessions.

Task source repositories use the deterministic branch `optimbench-baseline`.
It avoids collisions with upstream source paths such as a top-level `main/`; branch
names do not change the pinned root commit or tree.

Every task shares `tools/runner/runner.py`, copied byte-for-byte to
each image's `/app/workload/runner.py`. `check_tasks.py` rejects drift. The
agent-facing commands remain `sh /app/workload/build.sh` and `/app/build/run`.
Local execution has no timer. Public result formats and baseline hashes are
unchanged.

## Optional untimed in-container preparation

A protected task can declare this optional `task.json` field:

```json
{
  "container_prepare": [
    "/app/workload/guards/warm_git_objects.py",
    "--steps", "{steps}",
    "--inputs", "{inputs}"
  ]
}
```

The shared `DockerExecutor` discovers the task from its workload directory;
callers using a temporary workload overlay supply the protected task path
explicitly. The PID-1 shim executes the hook exactly once in each fresh case
container, before **any** timed step, including both operations of a paired
case. A/A qualification, scored A/B, correctness-only held-out checks and the
default executor share this path. Build/upstream/probe containers use it too:
hooks must be a no-op when their relevant workload steps are absent.

The executable must be a regular executable task-owned file under
`/app/workload`, outside `inputs` and the configured editable source tree.
`check_tasks.py` validates the declaration. Its image bytes must match the
protected task file's SHA-256 before execution; candidate source/build paths,
symlinks, and candidate environment overrides cannot supply the hook.
The hook uses a fresh fixed base environment, not candidate `PYTHONPATH`,
`LD_PRELOAD` or other runtime overrides.

Whole-argument placeholders `{steps}` and `{inputs}` become
`/judge-steps.json` and `/app/workload/inputs`. The step JSON has
`{"steps": [...], "container_prepare": {...}, "output": "/scratch"}`.
It is a separate read-only bind mount outside scratch, with no writable alias;
the selected case input root is also mounted read-only. The hook otherwise
uses exactly the container's timed-step mounts, CPU/memory/PID limits,
network isolation, read-only image, unprivileged user and scratch directory.
This supports untimed input pre-reading without exposing a candidate-controlled
preparation command.

The hook has a 600-second infrastructure timeout. Spawn errors, timeout,
nonzero exit, byte mismatch or live leftover processes invalidate the run
before the timed loop. Cleanup still freezes, kills and reaps descendants.
`container-prepare.json` plus complete `.stdout`/`.stderr` files record the
command, executable SHA-256, exit status, duration, streams and leftovers.
The JSON receipt is also carried in first-step telemetry and held-out
correctness records; failure errors retain the receipt. Its `timed: false`
duration is never added to `case_time_ns` or any step interval. A hook is not
an extra timing sample and cannot be enabled for only one arm.

Preparation receipts are validated independently (successful exit, exact
command/executable SHA, `timed: false`, valid streams, nonnegative duration
and no leftovers). PID 1 retains the original receipt in memory and rejects
edits to its scratch receipt/stream files during timed execution. Differential
observations contain only the library result, exit status, stderr and leftover
fields; preparation provenance is a separate `receipts` sidecar in reports
and reproducers, including shrink attempts. Untimed durations never enter
semantic equality or replay bytes. This does not exempt library bytes,
statuses, stderr or leftovers from comparison.

With no field, the executor retains the original list-form request, mounts,
command and receipts. No preparation process or receipt is created. Local
execution is unchanged.

## Interfaces

Each `workload/task.py` defines:

- `ENV`: fixed task settings, including original worker counts and warning filters.
- `cases(inputs) -> list[Case]`: authenticate content addresses and enumerate operations.
- `prepare(case, ctx) -> list[Step]`: untimed input verification/copying and command construction.
- `collect(case, ctx, results) -> dict[str, bytes]`: untimed canonical result serialization.
- Optional `compare(expected: bytes, actual: bytes) -> bool`: trusted, untimed
  semantic comparison of **complete results**, never hashes alone. With no hook,
  comparison remains exact length/SHA-256. `compare_results` rejects missing/extra
  keys and failure envelopes before invoking a tolerant hook.
- Optional `references(inputs)`: enumerate protected expected commitments for an
  alternative locked input layout. The default remains `documents(inputs)`.

`Case(id, data)` describes one operation. Terser and SQLGlot each use one case
for an entire sequential batch, returning 150 and 284 results respectively.
`Step(argv, cwd=".", env={}, timeout=600)` uses a scratch-relative working
directory. `StepResult` holds exit code (`None` on timeout), complete stdout
and stderr bytes, optional `t0_ns`/`t1_ns`, leftover process records and telemetry.
`Step(..., resident=True)` additionally implements the protected resident stop
protocol described below; existing steps keep their original whole-process behavior.
`Step(..., stdin_file="scratch-relative-file", home_cwd=True)` authenticates the
scratch-relative input path, opens it before the process-start clock, and sets
HOME to the fresh working directory. SQLfmt therefore launches the tool directly,
without a shell, timer, or per-task CLI supervisor.

Resident Rust batch workloads, such as serde-json, retain the original
manifest batch boundaries. Each batch preloads input bytes, stops ready, runs
sequential candidate API calls to fully materialized owned results, then stops
before export. No driver timer is used. Result identities map one-to-one to
the unchanged original manifest rows. SQLfmt retains the original
EXIT/STDOUT/FILE framing and checks normalized stderr as a separate exact result.

The original locked-case manifests enumerate public cases and exact file allowlists
separately. Input bytes are staged under `inputs/data/`; validation rejects
missing files, symlinks, traversal and altered bytes before any step starts.
Complete outputs use `named-files-v1` framing (`name NUL length NUL bytes`, sorted
by name). This preserves the original output SHA-256 values exactly.
`RESULT_ENCODING="named-files-v1"` makes displayed byte counts the sum of payload
lengths, as in those original receipts; SHA-256 still authenticates the full
unambiguous framed representation, including file names and lengths.

The numeric judge first runs the protected baseline and authenticates its exact
public commitment. Only that complete, authenticated raw result becomes the
comparison reference. Every baseline/candidate sample is then checked against it.
NumPy requires exact dtype/shape and uses `atol=rtol=8*eps(float64)`, equal NaNs;
storage order is not a semantic requirement. Malformed or failed results
cannot pass via tolerance.

## Resident timing boundary

Resident steps use one protected worker per case. The worker prepares
resident inputs, then stops itself with SIGSTOP. PID 1 observes readiness,
records t0 immediately before SIGCONT, and waits for the next SIGSTOP, recording
t1 on stop confirmation. It freezes all other processes before resuming only the
trusted exporter. Export, hashing, host comparison and cleanup are outside t0–t1.
Descendants remain stopped until final post-exit cleanup; they cannot compute during
the unmeasured export interval. Missing stops, early exits, timeouts or streams
remain incorrect candidate results, not successful operations.

NumPy includes the call, result materialization, metadata and in-memory `.npy`
encoding; prepared inputs, input hashes and the final file transfer are outside.
bzip2 retains a complete operation per subprocess and exact status/stream output.

These are plumbing boundaries, not timing qualification or performance claims.
The stop protocol is an external boundary that is subject to A/A and resource
qualification.

`Context` provides fresh case scratch, build/input/project paths, an optional
shuffle seed, `copy_project(name, inventory_sha256)`, `snapshot(tree)` and
`canonical_json`. Project copying verifies the full file/symlink inventory,
including extra files. Ruff's recorded upstream Git-tree inventory excludes `.git` implementation
metadata (fetch logs, packfiles); its original `.git` is copied unchanged for
ignore-discovery semantics. Every source file, including `.gitignore`, is checked.
Batch inputs are verified and copied before the child
starts; `cases.json` excludes expected outputs. A seed shuffles API-call order;
the public default retains fixed order. Result ids are always printed sorted:
`id<TAB>bytes<TAB>sha256`.

Every step gets a minimal environment: fixed PATH, fresh HOME/TMPDIR,
`LC_ALL=C.UTF-8`, `TZ=UTC`, plus task settings. Caller variables are not inherited.
`--outputs DIR` keeps raw per-step streams, metadata and complete canonical results.

## Post-exit cleanup and failure semantics

The clock stops when the main process exits (or times out). Before reading
streams/results, the executor stops and kills every remaining descendant it
can find, then reaps children. Leftovers are recorded, never a failure by
themselves. No cleanup wait or result collection enters the measured interval.

The shared Docker snapshot preserves command arguments and process state and
rechecks for zombie/dead (`Z`/`X`) status after metadata collection; a process
that exits during inspection is not a live leftover. Non-stdio pipe/socket FD
numbers are retained as classification metadata. The correctness-only held-out
mode compares these live-process identities/counts against pristine; the public
output gate continues to record leftovers without independently failing them.

Nonzero exit, timeout, stdout and stderr are compared candidate behavior, not
runner exceptions. CLI formats keep their existing canonical status/streams.
Successful API batches preserve their original raw result bytes. Abnormal
batch status/streams or missing results produce `workload-failure-v1` bytes
containing status, complete streams and any partial output, so they cannot
silently match successful baseline output. Only invalid inputs, missing
builds or broken runner/scratch/container infrastructure raise `HarnessError`
(invalid run, exit 2), never a candidate verdict.

## Executors

`LocalExecutor` launches one subprocess per step with file-backed output and
a new session. The runner is a Linux subreaper. At main exit it scans process
groups and the adopted descendant tree, SIGSTOPs leftovers (including detached
`setsid` helpers), records them, SIGKILLs and reaps. Timing fields remain `None`.

`tools/runner/docker_executor.py` is a public judge **prototype**, not a private
judge or scheduler. It launches one container per case, never per step:

```sh
python3 tools/runner/runner.py --task tasks/sqlglot-mozilla-transpile/environment/workload/task.py \
  --executor docker --image optimbench:sqlglot-mozilla-transpile --build /absolute/host/build \
  --inputs tasks/sqlglot-mozilla-transpile/environment/workload/inputs \
  --projects /absolute/host/projects --outputs /tmp/results --seed 31 \
  --memory 16g --pids-limit 512
```

Build/projects must be staged on the Docker host from the task's pinned image.
The build is mounted at `/app/build:ro`, fresh scratch at `/scratch`, and
`tools/runner/shim.py` at `/shim.py:ro`. The container runs as uid/gid 10000,
with no network, private IPC, read-only root, tmpfs `/tmp`, a memory limit and
a PID limit. `--cpuset 0-7` optionally fixes CPUs; omit it on unsupported hosts.
The image supplies pinned dependencies and protected workload helpers.
Host callers may pass `extra_docker_flags` (for example capability drops and
`--security-opt=no-new-privileges:true`) and a `tmpfs` mount specification to
`DockerExecutor`. These are trusted operator configuration, not candidate inputs.

The shim runs as PID 1. Per step it records `perf_counter_ns` immediately
before Popen and immediately after wait, stops all remaining processes with
`kill(-1, SIGSTOP)`, records count/cmdlines, kills them and reaps. It writes
`step-<i>.json`, including status, timestamps, leftovers, and cgroup
`memory.peak`/`cpu.stat` when readable. Case time is the sum of step intervals;
container startup, preparation, cleanup, hashing and collection are excluded.
Shim exit leaves kernel-enforced container cleanup. There is no scoring,
held-out data, scheduler or workload speedup claim in this prototype.

## Reproducible Git baseline

Every image initializes `/app/<library>` with the same author/committer
(`optimbench <optimbench@invalid>`) and fixed date
`2000-01-01T00:00:00+0000`. These variables are scoped to the initialization RUN,
not inherited by the agent's later commits. Git writes tree entries in canonical
order; `core.autocrlf=false` avoids host line-ending conversion and
`core.filemode=true` retains the pinned executable bits. The archives and
overlays supply fixed bytes/modes. Each Dockerfile asserts both the tree SHA
and root commit SHA recorded as `baseline_git_tree`/`baseline_git_commit`
in `task.toml`. Any source/mode drift fails the image build rather than silently
giving agents a different base. Unit tests independently reconstruct
the commit payload and check ordering/mtime independence and mode sensitivity.

To check independently of Docker layer caching, build the same task twice:

```sh
docker build -t baseline-a tasks/sqlglot-mozilla-transpile/environment
docker build --no-cache -t baseline-b tasks/sqlglot-mozilla-transpile/environment
docker run --rm --network none baseline-a git -C /app/sqlglot rev-parse HEAD
docker run --rm --network none baseline-b git -C /app/sqlglot rev-parse HEAD
```

Both commits must equal the task metadata pin; an OCI image digest need not
match because installed OS packages, build timestamps and other image data
are not claimed to be bit-reproducible.

## Public input caches

Canonical scoring, dedicated held-out checks, reference admission and the
direct verifier prepare authenticated public inputs before rebuilding the
pristine reference. A missing file in an input-free image cache may use its
declared pinned public recipe or an authenticated retained source body.
Existing corrupt cache bytes are rejected rather than silently replaced.
Git-history caches retain their original strict missing-or-corrupt rejection.

For source-side acquisition, run:

```sh
python3 -m judge.public_input_preflight --task TASK --fetch
```

The receipt distinguishes cache restoration, pinned acquisition fallback and
runtime-cache materialization. Every final file is checked against the
unchanged byte count and SHA-256 before use. Public notices outside the
workload are acquired without treating them as runtime data. Held-out
fetch-only references remain in the separate verifier view, never the public
workload, agent image or source checkout.

Task-specific protected derivations must still run under their original
pinned preparation environment. An acquisition archive is not automatically
an agent-visible input; preserve the task's original visible data roots and
required public project trees, including empty projects.

The canonical image builder disables optional Buildx provenance for private
preparation, while retaining the protected recipe label. Direct private
builds should likewise use `docker build --provenance=false`; audit image
indexes, configurations, attestations and every filesystem layer before any
publication. Fetch access is not a redistribution grant.
