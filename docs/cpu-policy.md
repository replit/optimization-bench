# Eight-CPU policy and total CPU reporting

All catalog tasks receive the same parallelism rule from
`tools/update_catalog.py:agent_instructions`, including legacy instruction
migration. Library code may use threads, SIMD and parallel algorithms within
the eight assigned CPUs. The driver's fixed concurrency is part of the workload,
not an optimization permission: do not change a driver's `--threads`, Black's
`BLACK_NUM_WORKERS`, `-p 8`, or other frozen arguments/environment settings.
Caching, recognizing benchmark inputs, interface breaks and leftover workers
remain forbidden.

The static runtime review does not block thread-count/affinity APIs merely
because they enable parallel work. GC, allocator, memory-limit, environment,
signal and VM checks remain. The post-call process-state observer retains
thread counts as diagnostics: extra post-call threads/goroutines still fail,
while fewer live threads are not a configuration violation. The existing PID-1
leftover-process check remains enforced. Resident runs also snapshot worker
identities at the ready/result stops and reject newly created workers still live
at the result stop, even if process exit would later hide them. Workers must finish before the measured
operation completes; this is not permission for persistent background pools.

## Accounting and interpretation

Each measurement record's `resources.cpu_time_usec` is the sum of per-step
container cgroup CPU `usage_usec` deltas. This includes worker CPU across all
cores and descendants, not just the main thread's user time. The process protocol
samples around process execution. For the resident protocol it samples at the
ready/result stops, excluding setup, result export and the separate preparation
hook. Cgroup observations include the shim's small boundary-observation overhead,
symmetrically in both arms. `container_lifetime_cpu_time_usec` retains the old
whole-container counter under an explicit name. Missing/nonpositive operation
CPU accounting invalidates a measurement instead of fabricating a ratio.

Wall-time speedup remains **baseline / candidate**. CPU-time ratio is deliberately
**candidate / baseline**, so a faster candidate with CPU ratio <= 1 is an
**efficiency win**, and a faster candidate with CPU ratio > 1 is a
**parallelism win** (including mixed optimizations that spend more CPU overall).
These labels describe point estimates, not a claim that the CPU CI excludes 1.
Without a wall-time win, the label is `no wall-time win`. Noisy or otherwise
inconclusive scoring is not classified as a win.

CPU summaries retain all paired A/B and A/A samples, their per-arm totals
(microseconds, excluding warmups), and 95% paired-bootstrap intervals. The
task-level CPU ratio weights cases equally in log space, like wall-time speedup;
the bootstrap resamples whole paired trials within each case. CPU accounting
does not change the wall-time adaptive stopping rule, estimator, noise gates or
calibration requirements. Total CPU is cost, not a replacement wall-time score.
The raw CPU estimate is still diagnostic when wall-time scoring is inconclusive.

## Affinity and native thread counts

Every task definition requests eight CPUs. The shared executor
passes the selected eight-CPU cpuset to Docker; registered runs must prove
their actual affinity. Task-local native thread-count settings are a separate
part of the frozen workload configuration, not permission to change affinity.
This policy does not change those settings or the task Dockerfiles.

Correctness-only cases remain mandatory but do not enter either wall-time or
CPU-ratio statistics.

## Correctness across CPU counts

`judge.cpu_policy` is the versioned release and instruction contract.
Correctness at 1, 2, 4 and 8 CPUs is required; official performance timing is
at eight CPUs. Low-core timing is an optional, report-only diagnostic, not a
regression gate, and the official judge does not execute it by default. A
missing or unverified CPU-subset receipt is not a passing correctness gate.
CPU-subset executions must be real, affinity-verified executions; they are not
inferred from thread flags or task resource requests.

`judge.cpu_correctness.check(task, baseline, candidate, image, out, *,
public_inputs=None, projects=None, builds=None, sources=None, runners=None,
limits=None)` replays the existing protected callbacks from
`judge.fuzzing_generic.task_runners(task)` and `RUNNERS`. It requests the
physical Docker cpusets `0`, `0,1`, `0,1,2,3` and `0,1,2,3,4,5,6,7`; it never
substitutes thread flags or remaps to other available CPUs. The judge host must
therefore reserve CPUs 0–7. Both arms and all existing replays are checked at
every subset.

CLI evidence uses the in-container `sched_getaffinity` observer and the
existing CLI semantic comparator. Header workers record the same system call
inside their container. Library callbacks use their isolated worker path and
must retain `diagnostics.execution_affinity` in each fresh or persistent
observation; library adapters without it are unmeasured, never implicitly
accepted. The executor does not add cases or substitute host-side library
calls.

Every subset records normalized per-arm execution identities, SHA-256-bound raw
gate evidence, admitted build commitments, the immutable Docker image identity
and a receipt accepted by `cpu_policy.correctness_receipt`. Counts refer to
executed observations (including replays), not unique generated inputs. Any
comparator mismatch, even on a single replay, is `contract_regression` with a
retained reproducer. Wrong actual affinity is `invalid`. Absent registrations,
unsupported applicable cases, partial pairs, missing observations, changing
case inventories and exceeded bounds are `unmeasured`. No subset may be
omitted. Scope exclusions require protected applicability evidence; a receipt
in which every gate is excluded and no case executed cannot pass. This receipt
does not replace the separate generic process-state gate.

The default limits are 256 case specifications per gate, 4,096 observations
per arm per subset and 900 seconds per subset. Limits fail closed rather than
sampling or truncating. These are resource deadlines, not performance
measurements: no timing score, calibration, A/A sampling or measurement entry
point is invoked.

A host-level proof for an admitted task:

```sh
bash tools/prove_cpu_correctness.sh \
  --task TASK --baseline ADMITTED_BASELINE_BUILD --candidate ADMITTED_CANDIDATE_BUILD \
  --image sha256:IMMUTABLE_IMAGE_ID --out FRESH_CPU_PROOF \
  --public-inputs PUBLIC_INPUTS --projects PROJECTS
```

The script does not build or authenticate candidates; upstream admission must
already have done so. It exits nonzero for invalid, unmeasured or regressed
evidence. Header tasks also need `--baseline-source-sha256` and
`--candidate-source-sha256` from admission.
