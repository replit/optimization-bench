# Fresh pristine-workload correctness validation

## Current qualification: real shared verifier path (2026-10-26)

**PASS: all three public families, both A/A and independently rebuilt-original
A/B lanes.** This is author-host, public-only diagnostic qualification,
not a held-out score, optimized speedup, registered calibration, or a claimable
result. The earlier bespoke qualification harness is retired.

Frozen source: `4d65bfcb28829c1c5196a7bc1cc23706ebb5d19a`.
Image: `sha256:6579f4c57a83909459eddd44c168b37629ec2107603d01441d9fb4cf5f675c63`.
Two fresh independent compilations (image baseline and actual `judge.build`)
produce byte-identical whole Git executables:
`e50fe67999641c76daa1113517fd4d840553dd5ba6196a9e713f3c1e97d8aa77`.
The build pins Git's install prefix and baseline version metadata, and maps
temporary compilation paths to `/build/git`. This removes build-time HOME,
missing `.git`, and random staging-path differences without changing upstream
source or optimization flags.

`real-path-protocol-20261026.py` calls `verifier.evaluate` with the real
scheduler/DockerExecutor path, identical per-arm paths/environment, a fresh
container and authenticated untimed preparation for each measurement, seed
1729, 30 fixed pairs per lane/family, one warmup pair per lane, and the native
5,000-draw whole-pair log-space bootstrap. There is no trimming, added pair,
rounding-to-pass, task-owned clock, or false separate-kernel attestation.

| Family | Lane | Median first / second arm (s) | Ratio | 95% CI | Full width (pp) |
| --- | --- | --- | --- | --- | --- |
| Myers | A/A | 14.645710 / 14.677552 | 0.999478 | [0.992892, 1.006795] | 1.390357 |
| Myers | A/B | 14.662304 / 14.572634 | 1.004445 | [0.996447, 1.011976] | 1.552844 |
| Shortstat | A/A | 5.495730 / 5.457079 | 0.999996 | [0.989076, 1.011111] | 2.203469 |
| Shortstat | A/B | 5.486450 / 5.481580 | 1.005015 | [0.999500, 1.010725] | 1.122483 |
| Histogram | A/A | 6.462477 / 6.493212 | 0.997482 | [0.989277, 1.005128] | 1.585053 |
| Histogram | A/B | 6.464955 / 6.461489 | 0.999268 | [0.992702, 1.006045] | 1.334278 |

Every CI contains 1 and has full width at most 3 percentage points. Every
measurement, including warmups, exceeds 1 second (minimum 5.243685186 s).
Both A/A arms use the baseline; A/B uses baseline versus the independent rebuild.
Table medians are grouped by lane, not chronological execution position.
`real-path-measurements-20261026.json` retains all 372 observations; the summary
retains full-precision decisions. Actual wall time was 14,089.291137039 seconds
(3 h 54 m 49.291 s), 01:10:47.761939–05:05:37.053214 UTC. Host-clear was released
immediately after completion; the six-hour cap was not discounted.

For **each family**, no session-wide noise flag was raised: steal fraction
0.0016714893322828611, sibling-busy fraction 0, throttle fraction 0.
Every family's 496 per-process cgroup readings report zero throttled usec.
The real verifier records host counters at session boundaries, not family
boundaries: family-specific host steal/sibling attribution is unavailable.
CPU-frequency readings are unavailable; no frequency-stability claim is made.

Fresh correctness evidence is prefixed `real-path-`: all three complete
observations equal their immutable references for baseline and rebuilt-original;
fresh broken production control is rejected in every family; API positive/ABI
negative checks, 139 exact selected upstream identities, native process state,
three-seed fuzz smoke, shrinking and two exact negative replays pass.
`real-path-public-admission-20261026.json` retains formal source/upstream
admission and the actual held-out-check refusal (`PAYLOAD PENDING`, operator
vault unavailable). No held-out inputs were fetched or fabricated here.
The independently constructed held-out set is delivered through the separate
builder branch, not these author-host measurements.

`aa-expanded-session-20261025.json` and its exact archived protocol preserve
the prior bespoke run: Myers/shortstat passed, histogram failed because its CI
excluded 1. Its unequal arm paths and distinct binary inodes were confounders;
the read-only diagnosis did not establish a unique cause. It is superseded
for qualification, not erased or reinterpreted as passing.

## Current expanded corpus

The current public population is Git / Cargo / Django / Prettier, with equal
25% commit counts and per-repository batches **4096 / 2048 / 2048**:
32,768 globally distinct commits. The four held-out repository reservations
are unchanged. `expanded-expected-twice.json` records two byte-identical
pristine output regenerations; `expanded-single-run-sizing.json` is planning
data only, not A/A qualification.

The expanded clean image is
`sha256:60d0b356ce2ff6f18b4ce326e9cbae68d848e8e5870d911bea0819c8032de591`.
Its authoritative receipts are `expanded-public-controls.json`,
`expanded-upstream-smoke.json`, `expanded-interface-smoke.json`,
`expanded-process-state.json` and `expanded-differential-smoke.json`.
They pass all three families' full-output/do-nothing/broken controls, 139
original upstream identities, original/ABI-negative inspection, native state,
three fuzz seeds, broken-edit shrinking and two exact replays.

All nine public control executions contain a validated `timed=false` receipt:
4 repositories, 8 pack/index files, 858,420,980 bytes read. Non-repository
upstream/fuzz operations carry explicit no-op preparation receipts. Static
header inspection does not execute a repository workload.

There has been **no A/A on the expanded corpus**. Review and a new host-clear
are required. Seven seconds was a sizing target, not a gate; the unchanged
hard minimum is one second. The larger discarded size probe was not selected.

## Archived earlier corpus

These correctness records cover the **new distinct-commit workload**, not the
archived identical-call shortstat workload. The subsequent A/A attempt failed:
`aa-failure-summary-20261025.json` and `aa-session-20261025.json` are the full
measurement record. No performance/measurability qualification is claimed.

The agent image is
`sha256:1a3cb60daf97268ecb65efb8cde4daa65828a38c89753cb3d770c1307ada723b`.
It was built from a clean environment-only checkout on the main/research
baseline. Its pristine canonical source root is
`92186d5b2a561afb5fe4b0dbce18a8906771faef`, tree
`79a1f663a05401ad749f6116a4898e03f2388323`, from upstream Git
`a018953688f1b10bddf91bff8747068f5f4746a4`. The normal pinned GCC/Rust recipe,
offline production build and clean-image public baseline gate passed.

## Observed outcomes

- `public-docker-controls.json`: all three units compared complete original
  observations against an independently rebuilt unchanged source. Every byte
  matched; the production-only broken control was incorrect in every unit.
  Full canonical observations were 156,274,047 / 244,451 / 75,643,279 bytes.
  Every repository process exited normally with empty stderr and no leftovers.
  These are correctness-only executions; incorrect controls receive no timing.
- `upstream.json`: all 139 exact TAP identities from the five selected original
  Git diff suites passed. `upstream-negative.json` rejects the broken
  production edit using its own copied binary. This is not the whole Git suite.
- `interface.json`: the unchanged original header/API surface passed; an
  independently built private ABI-field addition was rejected as
  `InterfaceBroken`.
- `differential.json`: seeds 1729, 2718 and 31415 all matched unchanged source;
  six families × 23 fresh-process atoms per seed (69 atoms/build). The broken
  production control mismatched, underwent deterministic greedy shrinking,
  and replayed twice with complete observations exactly equal to the saved
  original and broken observations. The shrink is not globally minimal.
- `process-state.json`: the original and unchanged rebuild had no native
  process-state differences; status `passed`.

The original helper patch for the private interface control failed compilation
because it prepended a field before positional initializers. The
`control-build-setup-failure.json` record preserves this non-qualifying setup
failure. Appending the unused field instead produced a built ABI-breaking
control; no task/baseline source or already-passing control build was changed.

All runtime verification used the shared runner/DockerExecutor or the standard
shared interface/upstream/differential entry points, network disabled, cpuset
0–7, with private controls and raw observations outside Git. No negative
control is an optimization. The protected case generator, exact comparator,
deterministic shrinker and fresh-process wrapper follow PR #17's pattern.

## Boundaries

`main-checks.json` preserves the initial main-tooling results. The later shared
count fix at `3e0aef982e4882f3809840ffdca38e538deb8ca2` was merged without editing
the shared tests locally. `main-checks-after-count-fix.json` supersedes those
old count failures: 194 judge tests and 135 tools tests pass, as do the
11 synthetic task-helper tests and branch CI. The coordinator still owns
Lumen's retirement and the final 20-task selection; Lumen was not edited here.

Held-out repositories are only reserved for a separate builder; no held-out
commit/window, expected result or benchmark instance was constructed.
No hacker, solver, paid model or optimized-candidate score ran. The one
coordinator-cleared A/A session stopped after the Myers CI width exceeded
3 percentage points; shortstat and histogram were not run. Every observed
Myers arm exceeded 1 second. Fresh measurement or protocol changes require a
new coordinator ruling and host-clear.

The pack-only preparation helper is now enabled through the approved generic
judge hook and its out-of-band receipt fix. `warm-untimed-qualification.json`
and the warm correctness records confirm exact outputs, controls, API,
upstream, native state and differential/replay checks, with validated receipts.

The warm-prepared A/A is archived in `aa-warm-session-20261025.json`. It stopped
after Myers narrowly but strictly failed the same 3-point width criterion:
3.010548 points. `aa-warm-failure-summary-20261025.json` records the result and
position statistics; the large prior first-position difference was absent.
No added pairs, reruns, trimming or rounding-to-pass occurred. The other
families were not run. The host-clear was released.

`aa-warm-variance-diagnostic.json` is read-only analysis, not a new run or an
excuse to remove observations. Batch growth is awaiting a corpus-mix ruling
because the pinned ripgrep population cannot support uniform growth. No
timing or rebuild is authorized at this checkpoint.
