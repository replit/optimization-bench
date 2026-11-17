# Held-out inputs, acquisition and public release

## Correctness-only held-out controls

Use a fresh output filename for each invocation:

```sh
python3 -m judge.heldout_check \
  --task tasks/<id> --image <existing-pinned-task-image> --cpuset 0-7 \
  --output /trusted/checks/noop.json
```

The default candidate is pristine source, rebuilt by the protected image recipe.
For a complete candidate source tree, add `--source /trusted/candidate-source`;
alternatively, `--artifacts /trusted/artifacts.json` accepts the existing JSON
map of editable source roots to exported directories. `--cache /trusted/cache`
can reuse authenticated image assets and upstream reference receipts.
This is the dedicated entrypoint. The compatibility alias
`python3 -m judge.verifier --heldout-check` delegates to the same command.

This mode authenticates an **available** held-out lock and all its smudged files
before doing any build work. It runs source-policy admission, the candidate
build, baseline/candidate upstream identity gates, and one exact-output pass per
held-out case on each build. Private project roots and locked workload metadata
overlays are honored; public input staging cannot replace held-out inputs.
Unsatisfied/unavailable locks, missing bodies, and LFS pointers fail closed.

No calibration, A/A or A/B sampling, scheduler, speedup, or performance statistics
are used. Ordinary runner isolation/resource enforcement still applies. The JSON
contains per-case/per-output match results, expected/observed lengths and hashes,
and `failed_cases`; output bytes are not copied into the report.
Exit codes are 0 for success, 1 for a candidate output mismatch, and 2 for an
invalid run (including a pristine/reference mismatch).

The report is correctness-only, `claimable: false`: it does not replace the full
interface/process-state/differential review or qualify a performance result.
Existing `--plumbing --dry-run` behavior stays public-input-only.

## Empty inputs and disjointness

Only zero-byte input files (SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`)
are exempt from input disjointness, because they contain no information.
`tools/check_tasks.py` reports their private/public paths separately under
`disjointness.exempt_empty_files`. Every non-empty collision, including a
one-byte input, still fails. There are no per-task exceptions or size thresholds.

## Import and release policy

Held-out sets are distributed where their licenses permit; that does not grant
third-party redistribution rights. A held-out set that is not distributed keeps
its original commitments, and an available lock is not marked unavailable for
that reason.

Retained bodies may be exported or released through LFS only with their source
notices and applicable redistribution obligations satisfied. Where rehosting is
not permitted or confirmed, keep exact pinned acquisition/derivation recipes
and authenticate the original bytes outside timing instead. Fetch-only verifier
references are acquired in an external temporary view, never retained in an
agent image or source worktree. A task-owned reconstruction command must produce
the original committed lengths/hashes, not generate replacement golden values.

An `available` lock can describe retained files plus fetch-only references.
Absent external reference directories in a clean source checkout are expected;
they do not establish that the dataset is missing or acquisition is broken.
The legacy catalog value `heldout-missing` can include this not-materialized
state. Actual acquisition and correctness receipts, not that local display
value, establish whether a fresh verifier execution succeeded.

LFS is storage, not an access-control boundary: anyone with repository/LFS
access can obtain its objects. Do not expose private history or unreviewed
bodies merely by changing repository visibility.

## Prepare an existing frozen task from a verifier-only packet

`tools.prepare_verifier_assets` is a **local-packet preparation** adapter,
not a public archive service, publication grant, task import or native
qualification. It does not establish release URLs. Permitted release assets
use one versioned per-task `tar.gz`, outside ordinary Git and all agent
contexts; no multipart scheme is needed unless a host imposes a size limit.
Originals above 100 MiB stay outside ordinary Git; this helper does not build,
fetch or upload a whole corpus.

Inspect the exact frozen roles without acquiring data:

```sh
python -m tools.prepare_verifier_assets --task tasks/TASK --inventory \
  > /verifier-preparation/required-inventory.json
```

`files` contains required retained data under `tests/heldout/`: the union of
retained payload entries and original lock files, excluding fetch-only INPUT
references. `source_required_files` is separate protected code/metadata and
must already match the original source. The packet may not supply or overwrite
code, guards, schemas, attributes or locks. Unavailable locks, unresolved
original declarations, missing protected source and unsupported directory
contracts remain explicit blockers, never permission to weaken checks.

Review release metadata **outside the frozen Task schema**. It must preserve
the inventory's `task`, original `heldout_lock_sha256`, `payload_sha256` and
exact `files` paths/SHA256s, with original per-file `bytes`. A lock-only file
without a payload size is reported as `null`; obtain its original size during
authorized preparation, never infer a trusted bound from an untrusted tar
header. Add `archive` with exact compressed `bytes` and `sha256`, and
`rights_basis` identifying the reviewed per-source permissions and required
notices. A nonempty label is not itself a legal grant: existing source notices
must be retained, and a release decision does not confer third-party
redistribution rights.
Only after a real permitted asset is published may `archive.url` name its
stable, credential-free HTTPS release URL. No private origin, signed URL,
credential configuration or private receipt may be a public setup dependency.

With an already authorized local packet:

```sh
python -m tools.prepare_verifier_assets --task tasks/TASK \
  --metadata /reviewed/verifier-release-metadata.json \
  --archive /authorized/original-verifier-assets.tar.gz \
  --out /new/verifier-only-checkout
```

All archive/file hashes, lengths and the frozen lock/payload identities are
checked. Members must use exact task-relative names such as
`tests/heldout/manifest.json`. Missing, corrupt, extra, duplicate, escaping,
linked, pointer-only and code/guard/lock-overwriting members fail closed;
expansion is bounded by the reviewed original per-file sizes. Only declared
parent directories are accepted. No files are unpacked over live code.

The result is a new one-commit, origin-free Git source view, not a no-Git scorer.
All members authenticate before it is visible. Declared payloads use the
existing transactional `stage_payload --scope heldout --from` implementation;
genuine lock-only data is restored too. The unchanged canonical
`heldout.check(require_available=True, allow_missing_references=True)` must pass
on that **prepared Task**, while fetch-only INPUT bodies remain absent. Their
original acquisition is separate and later. The helper runs no build, native
producer, model, sampling or score and does not qualify a task.

Run subsequent canonical judging **from the prepared checkout**, not the
original incomplete source directory; for example:

```sh
cd /new/verifier-only-checkout
python -m judge.heldout_check --task tasks/TASK \
  --image ORIGINAL_PINNED_IMAGE --cpuset APPROVED_EIGHT_CPU_SELECTION \
  --output /trusted/checks/new-output.json
```

Synthetic integration tests invoke the actual canonical archive consumer from
this prepared Git view, including a required lock-only file absent from
`payload.json`; the same consumer rejects the original missing-data directory.
These are fixture results; they do not establish that a permitted archive is
available, prove an anonymous bootstrap or qualify a task.

**Serde uses its separate route.** Its exact seven-file
`scorer-metadata/serde-json-production-crate-index-ingestion/` bundle and
image-owned pristine-producer recovery must assemble/authenticate all thirteen
originals before existing staging. The restricted six data-containing goldens
are not general release-packet assets. The packet adapter preserves an
existing authenticated seven-file sidecar when making another Task's source
view; it does not redesign that contract or add an agent-only smoke gate.

## Import verified originals

1. Install Git LFS (`git lfs install --local`). Recover the verified-final input
   files, original manifests, reference commitments and provenance from their
   verified original sources. Do not regenerate expected outputs or substitute
   data.
2. Replace the task's unresolved held-out entry in `payload.json` with its exact
   inventory: `scope: heldout`, source `name`, `destination` under
   `tests/heldout/`, original `bytes` and `sha256`. Stage original bodies:

   ```sh
   python3 tools/stage_payload.py --task TASK --scope heldout --from /verified/originals
   ```

   Staging authenticates bytes; it does not invent manifests, update expected
   results, or assert that the recovered set is the final set.
3. Restore the authentic `tests/heldout/manifest.json`, preserving original
   splits, docs, SHA-256 and expected length/hash metadata. Pin every private
   body and manifest in `tests/heldout.lock.json.files`, using paths relative
   to `tests/`. Reference bodies may use `heldout/references/`; expected-output
   bodies may use `heldout/expected/`. These body directories use LFS at
   every size. Keep manifests/locks as ordinary JSON, not LFS.

   Discovery-bound private project trees use `heldout/projects/<case-id>/`
   (also LFS). The verifier selects these only after checking the complete
   available lock; public projects are never substituted for private cases.

   Numeric/native cases use `optimbench-locked-cases-v1` descriptors pointing
   to private `heldout/cases.json`, `heldout/inputs.lock.json` and regenerated
   `heldout/baseline-outputs.json`, relative to `tests/`. Their data bodies
   belong in `heldout/data/` (LFS). Preserve content-addressed per-case JSON
   documents in `heldout/files/` and the ordinary `splits/docs` commitments too.
   Verifier-only parent/request metadata may live in `heldout/workload/` (LFS).
   The verifier creates an ephemeral host preparation view combining original
   workload code with only locked `.json`/`.txt` metadata; executable-file
   overrides and symlinks are rejected. Worker commands still use the unchanged
   pristine image's workload programs. Public runs never enable this overlay.
4. Authenticate all objects before changing the lock to `status: available`;
   remove the unavailable reason. `available` must be in the same reviewed
   commit as the complete inventory and LFS pointers, never a promise that
   objects will arrive later. Run:

   ```sh
   python3 -c 'from judge.heldout import check; check("tasks/TASK/tests", require_available=True)'
   python3 tools/update_catalog.py --task TASK --refresh-bindings
   python3 tools/check_tasks.py
   git add tasks/TASK
   git lfs ls-files
   git diff --cached --stat
   git lfs fsck
   ```

5. Confirm staged body entries are actual LFS pointers, not ordinary Git blobs
   (`git show :tasks/TASK/tests/heldout/files/SHA256`), and the pointers'
   OIDs/sizes agree with original byte commitments. Commit and push the
   reviewed import; the LFS pre-push hook uploads objects. In a fresh
   checkout run `git lfs pull`, repeat content checks, and requalify the task.
   Availability alone never means scored qualification.

An un-smudged pointer fails with
`held-out LFS object not fetched (git lfs pull)`, even if someone pins the
pointer's own hash. Verifier images need real bytes, but no `.git` metadata.
Large held-out files are exempt from the 1 MB retained-public-input-body policy
only when Git attributes mark them `filter=lfs diff=lfs merge=lfs -text`.
Fetch-only remains the default public input policy. Public LFS is allowed for
original inputs without an authenticated upstream, or where upstream fetching is
impractical (for example, inputs spread across many large archives), with a
provenance reason and the source, archive/member and SHA-256 recorded per file.
Recovered bodies live at `tasks/<id>/public/files/**`, never
under `environment/`. Each `payload.json` entry uses `kind: repo-lfs`, a
task-relative `source` under `public/files/`, exact `bytes`/`sha256` and `reason`;
it must not also have `url`, `fetch`, `transport` or `repository`.
Run a task-scoped `git lfs pull --include='tasks/TASK/public/files/**' --exclude=''`,
then `python3 tools/stage_payload.py --task TASK --fetch` to
authenticate and copy bodies into the existing public destination before
building. The build context contains materialized ordinary bytes, no LFS
pointer or repository metadata. An unsmudged pointer fails closed with
`public LFS object not fetched (git lfs pull)`. Existing large inventory
metadata is not an input body. These public bodies are not private assets and
remain subject to cross-task public/private disjointness checks.

## Agent isolation and CI

Every agent image is built with `tasks/TASK/environment` as its Docker context,
not the task/repository root. Tests exercise the real image-build entry point
and verify that no context path or symlink reaches `tests/`.
Never add LFS attributes or private payload destinations under `environment/`.
`check_tasks.py` checks cross-task private hashes against both public file
contents and digest references, including `payload-public.json`; it also
rejects private/public scope mismatches. Legacy unavailable placeholder
manifest hashes are metadata, not held-out data. Catalog refresh removes private
held-out path bindings from current agent-facing public-artifact inventories.

Disjointness is strict for inputs and dependencies: content-addressed case
documents, data arrays, project trees, per-case metadata and requests. A result
independently regenerated by the pristine baseline may legitimately coincide
with a public result. Such expected-output coincidences do not waive any input
check; they are reported with both case IDs in task provenance and task-check
output as `expected-output coincides with public output`.

Public-only CI sets `GIT_LFS_SKIP_SMUDGE=1` and checkout `lfs: false`. It runs
isolated fixture contracts that do not need original held-out objects.
Full catalog/task validation and scored verification intentionally require
fetched/authenticated objects after import; run those only in a job with
explicit LFS access. Do not cache or upload held-out bodies in CI artifacts.

## Redistribution and provenance

Each task retains its exact manifests, locks, input bytes, case membership and
golden commitments, together with task-local source and license notices and
tested acquisition commands. Unresolved source identities or license
obligations are blockers, not inferred permissions. Excluded and fetch-only
bodies are not exported, including copies in Docker or registry image layers;
input-free images use authenticated runtime packets prepared outside timing,
because a README restriction does not remove bytes from an image layer.

Copyrighted expected bodies are omitted only where the existing contract
supports complete pristine-output reconstruction against immutable expected
lengths and hashes. An expected commitment is never dropped to make
acquisition pass. Held-out acquisition is tested separately:
`judge.score --gates-only` is public-only and proves neither held-out
evaluation nor sampling.

Permission to release does not turn an unreviewed corpus
into redistributable data or a provisional result into an officially qualified
score.
