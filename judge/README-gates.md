# Real upstream and public plumbing checks

These checks run through `judge/` and the shared `DockerExecutor`, never directly
on the host or via a replacement per-backend supervisor. They are correctness
and transport checks, not speed measurements or held-out qualification.

## Recorded baseline contracts

| Task | Required outcomes |
|---|---|
| Babel | Main 29,505 passed / 18 skipped; polyfills 629; preset 61; historical 120/120 exit 0; target 109 passed / 8 expected failed, exit 1 |
| SQLFluff | 11,408 passed / 2,630 skipped, including the exact historical skip identities |
| SQLGlot | `Ran 1399` includes 57 skips: 1,342 passed + 57 skipped; 19,757 subtest observations; the two TPC fixture tests are declared exclusions |
| Ruff | 4,742 passed / 7 ignored, 42 all-feature suite summaries |

The guards in each task's `tests/upstream-expectations.json` supplement exact
identity/outcome comparisons; counts alone never establish parity. SQLGlot's
`unittest` total must not be added to its skipped count again.

## Execution

Build each image with its `environment/` as the context; stage the authenticated
Babel public delivery as documented in that task. The tracked Ruff Dockerfile
fix is in integration commit `81fa226`: its obsolete empty-driver COPY is gone.
Extract the image's pristine source, baseline build and public project trees to
trusted verifier-host paths. `docker create/cp` only transports those assets;
every build, suite and workload execution goes through the shared executor.

    python3 -m judge.gates --task tasks/<id> \
      --pristine /trusted/pristine --source /trusted/source \
      --baseline /trusted/baseline-build --candidate /trusted/candidate-build \
      --image <task-image> --cpuset 0-7 --output /trusted/upstream.json

The gate writes `passed`, `rejected`, or `invalid run` and preserves stdout,
stderr, step metadata and available identity reports beside the receipt.
`--baseline-receipt` reuses only a trusted successful baseline with matching
task, immutable image, source and build commitments; recorded baseline guards
are checked again. `--fail-fast` can shorten deliberately broken controls.
Actual failed test records, not an unrelated build/import error, are required
by the receipt collector to call a negative control successful.

Editable-root artifacts can be built with `judge.build --artifacts roots.json`;
SQLGlot's plumbing includes this full replay/build/verifier path. Both source
admission and the resulting runtime are checked. Python upstream suites must
import `/app/build/runtime`, not silently fall back to the source checkout.
The runtime's `bin/` is on the actual child PATH so SQLFluff's diff-quality
tests can resolve its installed console script. The example plugin is installed
offline and SQLFluff receives `SQLFLUFF_TESTENV=1`.

Ruff uses `--all-features`, the declared supported crates, eight pinned CPUs and
UID 10000. Its suite headers and test records are merged at execution time so
each identity keeps its real Cargo target. The optional `--cache-dir` is
source/image/profile-keyed and verifier-only. `judge.build --seed-build` can
reuse a prior Ruff compiler cache only after verifying its task/image and both
source/build commitments; changed source mtimes are advanced for Cargo.

SQLGlot subtests can shuffle their order and include default Python object-repr
pointers. Raw observations and their original order are retained. Comparison
uses an identity/outcome multiset, preserving duplicates, and normalizes only
the process-local pointer in a default object repr. Literal hexadecimal SQL
parameters are unchanged.

## Evidence

`python3 tools/replay_tasks.py --output /trusted/runs` records image/executor parity.
Add `--controls-only --with-upstream` to execute output-changing candidates and
the selected upstream suites. All evidence remains in that external output
directory; task packages do not retain execution logs. Inspect the full
source/image/build-bound ledgers for every test identity.
Source-control patches use zero context; apply them to the pinned pristine tree
with `git apply --unidiff-zero`.

Ruff uses distinct negative controls: suppressing diagnostics breaks upstream
tests, while explicitly changing stdout proves rejection of altered public
output even where the baseline command emits no diagnostics.

All receipts remain `claimable: false`; the original held-out sets they used
are unavailable.
