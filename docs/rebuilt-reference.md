# Rebuilt-pristine scoring references

Scoring compares a submission with an independently built pristine reference,
not with the artifact produced while assembling the task image. Both builds use
`judge.build`, the same protected image recipe, `/scratch/source`,
`/scratch/build`, fixed HOME/TMPDIR, task build environment and source envelope
rules. Neither copied source tree contains `.git`. Compiler-cache seeding is
disabled for reference builds. Build and admission work is outside measurements.

This removes a systematic build-context confound. It does **not** guarantee
reproducibility: timestamps, random generation or host CPU autodetection can
still change native code. A/A uses the same admitted reference in both lanes;
it cannot discover reference-versus-candidate build differences by itself.

## Admission and immutable cache

`judge.reference.ensure(task, trusted_assets, image, cpuset, cache)` returns an
admitted reference descriptor, including `build`, `root`, `key` and
`build_sha256`. The caller obtains image assets with `judge.score.assets`.

The separate `judge-rebuilt-reference-v1/` namespace leaves the old image cache
unchanged. Its key includes task ID, immutable image ID, admitted pristine
source digest, host build/admission helper digests and protected task/config/
output-lock commitments. A per-key file lock serializes writers. A fresh stage
is atomically published only after:

- Image-pristine public interface preservation.
- Every frozen public output matches exactly.
- Every frozen held-out output matches exactly when available.
- Original upstream identity/outcome ledgers and protected count contracts pass.

The image's pristine source remains the source-policy anchor. Upstream
admission compares rebuilt pristine against the original image ledger, not
against itself. A qualification failure is an invalid task run, not a candidate
regression; expected output files are never rewritten.

Every read verifies descriptor identity, built source/artifact inventories,
build receipt and admission receipt hashes. A corrupt entry fails closed;
there is no silent cache repair or fallback to the image baseline. Candidate
upstream receipts and registered calibration are also bound to the reference
key and build digest. Old image-baseline receipts cannot authorize the new run.

To materialize the reference without measuring performance:

```sh
python3 -m judge.reference --task tasks/TASK --image TASK_IMAGE \
  --cpuset 0-7 --cache /trusted/cache --output /trusted/reference.json
```

The task image must already exist. The output descriptor must be fresh.
Available private inputs must be materialized/authenticated; fetch-only inputs
use the existing external acquisition mechanism.

## Call sites and author-side diagnostics

`judge.score` selects this reference consistently for interface, differential,
process-state, upstream and scheduler calls. Its separated-host requirement is
unchanged. `judge.heldout_check` uses the same reference for upstream admission
and exact comparison. The scheduler itself is unchanged: A/B selects reference
or candidate, and both A/A lanes select the reference.

The direct scored verifier CLI selects/adopts the reference from
`--reference-cache` (default `/tmp/optimbench-score-cache`). An optional
`--baseline` must name that exact admitted build, not an extracted image
baseline. The legacy `judge.harbor` entry forwards `reference_cache`; it no
longer selects the image baseline from old run configuration.

For a direct Python caller, pass the descriptor as `reference_info=reference`
and `baseline=reference["build"]` to `judge.verifier.evaluate`. Generate the
submission's upstream receipt with `judge.score.upstream_gate(...,
rebuilt_reference=reference)`; it binds the candidate and reference inventories.
Passing an image baseline or an unadmitted reference is rejected before timing.

An author-host diagnostic can call `evaluate` with `provisional=True` and a
real `execution_host=judge.score_host.select(image, cpuset)`, without calling
`require_separate` or inventing a separation attestation. Mark that output
`mode="qualification"`, `author_host=True`, `diagnostic_only=True`,
`claimable=False`, and `calibration_eligible=False`. It is not separated-host
scoring or registered-host calibration. Retain the normal admission checks.

Explicit public transport-only plumbing still accepts an explicitly supplied
baseline and does not acquire private inputs. It is never a score; no scored
caller falls back to that mode or implicitly picks an image baseline.

## Intentional image oracles

The image still contains `/app/baseline-build`. XZ's differential probe uses it
for the tiny-buffer capacity oracle. Such oracles are intentional independent semantic anchors,
not timed scoring arms. Their oracle mounts and existing replay/image commands
are unchanged. Do not redirect these capacity oracles to candidate artifacts.

## Two-build native qualification

```sh
python3 -m judge.build_qualification --task tasks/TASK --image TASK_IMAGE \
  --cpuset 0-7 --cache /trusted/cache --out /fresh/qualification
```

This builds pristine twice through `judge.build`, without a compiler-cache
seed, source edits, scheduler, or timing claims. It compares whole-file SHA-256
and ELF64 SHF_ALLOC contents, executable code, sizes, addresses, offsets and
alignment. It covers exported native programs/libraries and all packaged
native Python extensions (a conservative superset of per-case runtime loads).
Static libraries are represented by their linked executable; intermediate and
test build trees are excluded.

The JSON reports `identical`, `acceptable-with-disclosure`, or `flagged`.
Same-length date/time strings with unchanged code/layout are disclosed, not
flagged. Debug/build-ID-only changes are disclosed separately. Any executable
code/layout change, or other loaded data change, is flagged for task-maintainer
review. A flagged report exits nonzero; it never edits expected outputs,
task settings or candidate verdicts.
