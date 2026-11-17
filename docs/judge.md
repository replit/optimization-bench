# Separate clean judge

The optimizing agent receives only its task's `environment/` image. Evaluation
uses the protected task driver, source/interface policy, original upstream tests
and held-out data from a separate trusted checkout. Do not mount the repository
root, verifier files, held-out inputs or Docker socket into an agent container.

## Source transport and reconstruction

`task.toml` `artifacts` declares the editable source exports matching
`release.json`'s source policy. NumPy exports `numpy`, SQLite exports `src` and `ext`, SQLGlot exports
`sqlglot`, and Git exports `xdiff`. The judge validates export paths and kinds,
rejects symlinks or missing exports, and rebuilds admitted production source in
the pinned task image. Dependencies, drivers, flags and source pins stay
protected. Local test edits are not submitted in place of the original tests.

`judge.reference` builds a pristine reference from the protected source rather
than trusting an agent's baseline artifact. Cache identities include protected
recipe/source intent and the actual runtime image. See
[rebuilt-reference.md](rebuilt-reference.md) and
[task-format.md](task-format.md).

## Correctness and timing are separate

The pipeline records source admission, interface compatibility, upstream
outcomes, differential behavior, runtime-state review, exact output contracts
and the registered generic fault/lifecycle gates. Missing registration is not
a pass, and registration alone does not prove native execution or applicability.
Those required gate-applicability records are distinct from the optional
scoring-host registration described below.
The [gate policy](v1.1-scoring-policy.md) selects headline versus report-only
receipt groups without rewriting their observations.

The public and held-out manifests freeze inputs and complete expected results.
NumPy uses its protected numerical comparator, not arbitrary hash equality.
SQLite, SQLGlot and Git retain their full typed/serialized/byte-exact contracts.
Input staging, authentication and specified preparation are untimed; measured
boundaries remain those in each task's driver and
[`tests/judge.json`](../tasks/numpy-quantile/tests/judge.json).

`case_roles` may mark cases correctness-only. Only declared performance cases
enter `performance_units`, with equal-weight task aggregation. The current
NumPy and SQLite case-role reductions are retained; removed timing units remain
correctness checks, not discarded workloads.

The runner uses the process-exit or resident-stop protocol appropriate to the
task. It includes required output materialization and rejects work left running
after the public call or CLI exits. See [runner.md](runner.md).
Sampling, four-lane A/A controls, stopping rules, bootstrap intervals, minimum
unit duration and host-noise checks are unchanged; see
[statistics-policy.md](statistics-policy.md).

## Local/provisional evaluation

Local artifact-based `judge.score` needs no account, registered machine,
image registry login, host-review approval or session-control receipt. If optional
host registration is absent or denied, the judge still runs actual correctness,
local interleaved A/B and A/A, and numeric interpretation with
`claimable:false`. This is not a dry run or a registered cross-host score.

Install the locked host dependencies and prepare the actual task source and
authorized public/required held-out assets as described in the root README.
Use Docker in a genuinely isolated scoring context: an Intel x86-64 Linux
machine matching the documented scoring configuration (8 dedicated judge CPUs,
16 GiB, SMT off); see [scoring-host-setup.md](scoring-host-setup.md). The judge probes the actual execution kernel, selects
exactly eight observed CPU IDs and records whether pinning took effect.
Unsupported or ineffective pinning does not qualify as an eight-CPU result.

Export only the task's declared editable production roots, not the repository,
task environment, protected tests, drivers or dependencies. The JSON keys must
match the complete exact set in `release.json`'s `source_policy`; NumPy's
`/ABS/artifacts.json` is:

```json
{"numpy": "/ABS/exported/numpy"}
```

The values name the exported production directories. Set `TASK_IMAGE` to the
actual verified immutable task image ID/digest available on the scoring host;
do not guess a registry URI. Replace the absolute paths and choose an output
directory that does not exist:

```sh
: "${TASK_IMAGE:?Set TASK_IMAGE to the actual pinned task image}"
python3 -m judge.score \
  --task numpy-quantile --artifacts /ABS/artifacts.json \
  --mode provisional --separate-sandbox --stop-on-gate \
  --image "$TASK_IMAGE" --out /ABS/FRESH-result
```

`--separate-sandbox` truthfully attests isolation when a comparable agent
kernel fingerprint is unavailable. It is not permission to use the agent's
known same kernel or fabricate a different fingerprint. When comparable
identities are available, `--agent-host-fingerprint` supplies the actual
agent kernel identity; the judge rejects matching agent/scorer kernels.

`--image` reuses the actual pinned image; without it, the judge builds the task
image. Neither path proves anonymous source/image availability or licensing.
`--gates-only` runs untimed gates without sampling. The default `v1.1-rc`
mandatory gates (an inherited gate-profile identifier that v1.0 uses unchanged) and pristine source/image reference admission remain intact;
explicit legacy is diagnostic-only. The example changes no protected task
pair counts, warmups, precision, clocks or noise/bias thresholds. Its A/A is
genuine current-session baseline control, not copied historical calibration.

The default score-through mode records review flags and continues measurement
where supported. It restores protected paths after out-of-scope edits and keeps
those edits flagged; it does not approve them. `--stop-on-gate` restores stopping
at a failed gate. Incorrect outputs, build/admission failures or review flags
never become a clean, claimable score merely because timing continued. Read
`result.json`, `summary.txt`, gate details and exit status together.

For a clean local correctness/numeric result, check the actual output fields:

- `result.json`: `correct:true`, no entries in `flags`, and `needs_review` not true.
- `result.json`: `host.pinning_took_effect:true` under the original eight-CPU policy.
- `result.json`: `quality.noisy:false`.
- `measurements.json`: `aa_passed:true`.
- `result.json`: `aa_precision.passed:true`.

`result.json` contains interpreted `optim_score`, `speedup`, `candidate_ci95`
and `aa_noise_floor`. An inconclusive A/B result, ineffective pinning, failed
precision/noise checks or neutral `optim_score:1.0` is not a demonstrated gain.
On unregistered hosts, `measurements.json` is `diagnostic_only:true`; do not
aggregate those raw measurements into reviewed release/cross-host claims.
Missing registry eligibility recorded in `registration_attempt` or
`claimability.reasons` explains `claimable:false`, not a waiver of any gate.
Retain the actual image/source/build identities, host fingerprint, protocol
and result scope. Keep CPU classes, provisional/registered modes and
`public_only` versus held-out scope separate. This command/example is not a
live host, bootstrap, rights, performance or all-task qualification.

## Held-out data

Recover only authorized task-scoped LFS bodies. Fetch-only inputs are acquired
and authenticated outside the checkout by the trusted verifier, not copied into
the task image. Expected-output bodies and lock manifests remain protected.
Missing data fails closed; do not substitute public inputs or invent receipts.
See [heldout-lfs.md](heldout-lfs.md).

An already prepared task image can run untimed pristine held-out correctness
checks without starting a performance campaign:

```sh
python3 -m judge.heldout_check \
  --task tasks/sqlglot-mozilla-transpile \
  --image <existing-pinned-task-image> --cpuset 0-7 \
  --output /trusted/heldout-noop.json
```

Adding `--negative-control` deliberately perturbs a result to test rejection.
Its expected failure is a control result, not candidate qualification.

Legacy: each task's `tests/test.sh` calls `judge.harbor`, an entrypoint kept
from an earlier Harbor-based agent harness. Harbor is no longer used; score
candidates with `judge.score` as above. When `judge.harbor` is invoked, the
trusted host supplies `JUDGE_RUN_CONFIG`; the agent cannot supply its own
verifier configuration.

## Qualified scores and wrapper distinction

The host registry (`judge/hosts/registry.json`) records the reference scoring
configuration: an Intel x86-64 Linux machine with 8 dedicated judge CPUs,
16 GiB and SMT off (the recorded reference machines are `c4-standard-32` /
Xeon Platinum 8581C instances). A run is marked `claimable` only on an exact
registered host or a fresh machine whose fingerprint matches that recorded
configuration, with a valid same-boot pristine control. On any other machine
the judge still scores fully, with `claimable:false`. A claimable run still requires a clean tagged release, matching
judge/task/image identities, all mandatory gates, local Docker, current A/A and
clean host quality. A pristine control alone remains provisional. See
[host-registry.md](host-registry.md) for the full registration contract.

Historical qualification receipts under `judge/hosts/` are provenance only;
they do not qualify the current judge or task identities.
