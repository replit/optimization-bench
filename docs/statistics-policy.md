# Sampling and timing interpretation

## Frozen settings, explicit overrides

Each task's sampling settings are frozen in its `tests/judge.json`. Every task
declares 5 minimum / 15 maximum pairs, one warmup and a relative CI half-width
of 0.02. The score/verifier CLI defaults and direct verifier API resolve
minimum, maximum, warmups and relative CI half-width from `tests/judge.json`.

An explicit CLI/API override is allowed and retained under `sampling_policy`:
`declared`, `effective`, `explicit` and `overrides`, with
`version: task-sampling-policy-v1`. The scorer records the original CLI intent
rather than mislabeling its internally resolved defaults as user overrides.
For an explicit 30/30 rescore:

```sh
python3 -m judge.score ... --minimum 30 --maximum 30
```

## Adaptive stopping

After the task minimum, stop early only when:

1. Both A/B and A/A meet the requested relative half-width.
2. A/A's **full 95% CI width is at most 5 percentage points** and A/A is not
   biased (see below).
3. A/B's CI clears the measured A/A envelope, including its offset from 1.

### A/A bias across units

A task with k performance units tests k A/A arms. Requiring every 95% CI to
contain 1 would flag at least one fair unit in 1 - 0.95^k of sessions (30% for
k = 7). The per-unit bias decision is therefore Bonferroni-corrected at
family-wise alpha 0.05: a unit is biased only if its (1 - 0.05/k) bootstrap
interval, read from the same draws as its 95% CI, excludes 1. With k = 1 this
is the 95% CI. The scheduler's early stop and `aa_passed`, the scorer's
`aa_unresolved_or_biased` veto and `tools/judge_control.py` all use this
decision. Each unit's interval is recorded as `aa.bias_test` in measurements
and `aa_noise_floor[unit].bias_test` in results, with `aa_bias_test` giving k,
the per-unit alpha and the biased units. A missing or inconsistent record falls
back to the stricter 95% CI. The 5 pp precision bar, the A/A noise envelope and
the A/B verdict still use the unadjusted 95% CI. The percentile bootstrap is
slightly narrow at 30 pairs, so the realized family-wise rate for seven fair
units is about 7-9% rather than exactly 5%.

### Practical-equivalence band

Two identical builds are never timed exactly alike: memory layout, file paths
and cache placement shift one copy by a fraction of a percent. With enough
pairs such an offset becomes statistically detectable although it is
irrelevant. An A/A unit is therefore also **centred** when its 95% CI lies
entirely inside **[0.975, 1.025]**, the same ±2.5% tolerance as the 5 pp
precision bar. The verdict-level decisions use this rule
(`judge.score_result.aa_centred`): the scorer's `aa_unresolved_or_biased` veto,
the host registry's `session_quality_unclean` claimability check and
`tools/judge_control.py`. A unit outside the band must pass the family-wise
test above. The scheduler's early stop and its `aa_passed` summary still use
the statistical decision alone, so sampling is unchanged. The band cannot
create a speedup: a candidate must still clear the measured A/A envelope,
including its offset from 1.

Otherwise continue to the task maximum. Reaching the maximum does not turn
bias, noise, or an unresolved effect into a gain. All observations, including
warmups, remain in the receipts; none are trimmed. `session_stop` explains why
each unit stopped. `aa_precision` reports the A/A precision limit and per-unit flags.
The limit is 5 pp for every task, unless a task declares a different
`aa_max_full_width_pp` in `tests/judge.json` (finite, positive, at most 10). The scheduler's A/A stopping rule, `aa_precision`, the
scorer's `aa_precision_above_bar` veto and `tools/judge_control.py` use 5 pp;
no task currently declares its own limit.
A precise but off-center early A/A therefore gets the remaining budget to
recenter; persistent bias remains inconclusive at the maximum.

The A/B wall summary bootstraps complete pairs within each performance unit in
log space, weighting units equally. It never pools CPU classes or substitutes
independent worker samples for paired trials.

### Multi-unit headline

For a task with more than one performance unit, a unit whose candidate interval
stays inside its own A/A noise floor is listed in `unit_notes` and does not by
itself make the result inconclusive. The equal-weight aggregate interval must
still clear the combined A/A envelope. Per-unit A/A quality gates (unresolved
or biased A/A, or a full width above the A/A precision limit), host noise and
paired precision still veto the result. Slowdowns remain in the aggregate with
equal weight.

A task with a single performance unit keeps the per-unit veto: if its candidate
interval stays inside its A/A noise floor, the result is inconclusive
(OptimScore 1.0).

## Significant but imprecise gains

If correctness and all host/A/A/noise checks pass, the A/B CI excludes 1 and
clears A/A, but the requested A/B precision is still unmet at the cap, report
`speedup_imprecise`, not noise-inconclusive. The attributed `speedup` and
`optim_score` are the conservative lower confidence bound. `point_speedup`
retains the raw estimate; `reason_codes` includes `paired_precision_unmet`.

For example, a SQLGlot A/B CI of `[1.044, 1.101]` with clean A/A
`[0.9925, 1.0075]` yields `speedup_imprecise` and OptimScore **1.044**, not 1.
Host noise, biased/wide A/A, or an interval not clearing the noise envelope
still yields inconclusive and no attributable gain. A correctness failure is
never converted to a timing success.

Resolved results also expose their LCB as `optim_score`, separate from their
point estimate. Task/CPU-class aggregation consumes OptimScore; raw
measurement diagnostics still cannot be mixed with interpreted results or
pooled across CPU classes.
