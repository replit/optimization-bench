# Task format

This is the single source of truth for every task. A task can have a complete shell while its
public or held-out payload is unavailable. Never fabricate inputs or outputs to
make it appear ready.

Modify-only source scopes (`"operations": ["modify"]`) require an explicit
`allowed_files` list, including when the scope names a directory. A directory
root is not an implicit production-file allowlist. Missing, null or non-list
allowlists fail closed with `modify-only directory scope requires allowed_files`.
The submitted file set must exactly equal that allowlist; protected tests,
fixtures and snapshots remain outside the editable production scope.

## Public interface compatibility

Interface admission checks backward-compatible public contracts, not equality of
implementation graphs. JS/TS retain public module exports and public members of
exported classes/objects. Leading-underscore names and `#private` members are not
public. Non-exported prototype ancestors/helpers are excluded; inherited package
class members are included when the base class is itself publicly exported.
Node/core identities remain opaque. Constructors are not executed to discover
conditional instance fields; runtime introspection covers reachable object,
prototype and static members, with typed source checks where configured.

Every baseline public name must retain its kind. Existing source-available
positional parameters must remain in order; required parameters may become
optional, and new optional trailing default/rest parameters are allowed. New
exports/members are allowed. Required counts must not increase. Existing
defaults and destructuring shapes are conservatively preserved; local JS
parameter renaming is ignored, while moving retained binding names between
positions is rejected. Types erased by JS require a TS source component; its
existing parameter/return types and generics currently require exact matches,
not inferred subtype compatibility.

Python similarly allows new optional positional/keyword-only parameters and
variadics, while retaining existing callable parameters, annotations, defaults
and keyword names. Signatures that cannot be parsed remain exact/opaque.
Rust/Go/Java/C declarations remain exact; their configured signature forms do not
express default/keyword argument additions. C++ stays ABI-conservative: adding a
defaulted positional argument can change a mangled symbol, so existing
declarations/overloads are retained rather than inferring compiler-level ABI
compatibility. New overloads/public additions remain allowed in every language. Opaque native/bound
JS functions expose only kind/arity, not a complete positional/type contract.

JS identity aliases avoid expanding cycles and diamonds. Depth/node bounds
produce explicit truncation markers; any truncation is incomplete coverage and
fails closed, even if both markers match. This is a conservative admission aid
alongside correctness tests and source review, not a full semantic ABI proof.

## Agent working rules

`instruction.md` contains a generated working-rules section maintained by
`tools/update_catalog.py`. Its budget comes from `task.toml`'s agent timeout:
all tasks currently allow two hours (7,200 seconds). The source present at the
time limit is judged. The rule is neutral and does not identify optimization
targets.

New tests and fixture files may be added, including new cases in existing test
files. New tests must pass; skip/xfail markers on new identities are allowed,
but failures and errors are not. Every original test identity and outcome
(including skip, xfail and deselection) must remain exactly unchanged.
Existing tests, fixtures, snapshots and expected outputs must not be modified,
removed or renamed. Existing protected test source permits additions only;
fixture/snapshot bytes remain immutable. Added test/fixture content is bound
into source receipts.

Library imports and calls must preserve the caller's process-wide configuration:
no global GC/allocator tuning, memory limits, thread/process counts, environment
writes, interpreter/JIT/VM flags, signal handlers or similar side effects.
Added public items are permitted; removing or incompatibly changing an existing
public item is not. The judge records added runtime-configuration matches as
`needs_review`, without an automatically attributed speedup.

## Public input distribution

Held-out body storage, acquisition and release are documented in
[Held-out inputs, acquisition and public release](heldout-lfs.md). Private LFS
is never permitted inside an agent environment.

Public LFS is allowed for original inputs without an authenticated upstream,
or where upstream fetching is impractical (for example, inputs spread across
many large archives), with a provenance reason and the source, archive/member
and SHA-256 recorded per file. Declare
`kind: "repo-lfs"`, `source: "public/files/<name>"`, `reason`, exact `bytes`
and `sha256` in the existing public payload entry. Do not combine this kind
with any upstream URL/recipe. Bodies use `tasks/<id>/public/files/**` and the
repository's exact LFS rule, never a path under `environment/`. After
`git lfs pull`, `stage_payload.py --task <id>` authenticates these bodies and
materializes the unchanged declared environment destination before Docker
build. Public-only builds need no held-out objects. Missing or unsmudged
public bodies fail closed; workloads never acquire data during measurement.
Authenticated upstream fetch remains the default.

Follow Terminal-Bench 2's convention: small inputs stay in ordinary Git, with a
guideline of **at most 1 MB per file and approximately 5 MB of public input bodies
per task**. Larger public data is acquired in the Dockerfile's build-time guard
from original upstream sources. Recipes pin an exact URL at a fixed commit,
package/dataset release or publisher asset, byte count and SHA-256. Every final
input must also match its unchanged original full-input size/SHA-256 before use.
Source outages or changed bytes fail closed; no replacement corpus is permitted.

The guard supports direct files, pinned archive members and deterministic byte
framing. Thus sqlfmt's original CLI1/tar bytes need not carry large source
bodies in Git. Where a PNG publisher regenerates `tIME` and `date:*` text
chunks, the recipe authenticates every other PNG byte, restores only those
small recorded timestamp chunks, and requires the original complete SHA-256. No pixels, input bytes or expected results change. Small
frozen snapshots whose current publisher cannot be used remain in Git within
the size guideline and are explicitly listed in the source report.

`payload.json.files[].fetch` is the build-time recipe, copied into the public-only
`environment/payload-public.json`. The Dockerfile runs the byte-identical
`payload_check.py --fetch`, then copies its authenticated workload into the image.
Runtime containers never download. Use operator staging only for files without
a reproducible public upstream and for private held-out material; this does not
introduce a generic payload store.

Each Task's prebuilt image is published as
`public.ecr.aws/h1f5v5u6/optimbench:<task>-1.0.0` and pinned by digest in
`task.toml` at release; NumPy and SQLite are built locally from `environment/`.
The Dockerfile remains the reproducible recipe; held-out data never enters an
image.

## Canonical guard versions

`tools/payload_guard_versions.json` registers exact released standalone guard
SHA-256s and their supported top-level payload kinds. `check_tasks` accepts an
older copied canonical guard only when it supports every kind declared by the
task, reporting `guard version: <id> (older canonical)` as information.
URL recipes are file transports covered by the file capability; git-history
requires a version explicitly supporting that directory kind. Unknown/modified
guard hashes, unsupported kinds and an unregistered current tools guard fail
closed. This registry does not rewrite frozen task guards, provenance, build
contexts or image digests. New canonical guard releases must register a new
hash/capability entry rather than invalidate compatible frozen packages.

Known-broken releases may be explicitly revoked for all payload kinds. The
`git-history-v1-portable` release (`980aa113…`) is revoked: its standalone CLI
read private `payload.json` after successful public validation, breaking both
file and Git-history image builds with public-only contexts. The original
`url-recipes-v1` and `git-history-v1` releases do not have that bug and remain
registered. The current `git-history-v1-public-cli` uses `payload-public.json`
for acquisition, checking **and** receipt output; the private manifest is never
an image-build input.

## Explicit `git-history` directory payload

This public, fetch-only type is for workloads consuming Git history itself.
Private histories use the same integrity/acquisition rules through the
verifier-only reference mechanism described below, not the public build guard.
It is **not** an archive/file payload and its integrity hash is **not** the
SHA-256 of publisher pack/bundle bytes. Pack order/compression may vary.
Declare it in `payload.json.files` with these fields (placeholders below must
be replaced; strings are not accepted for measured integer counts):

```json
{
  "kind": "git-history",
  "name": "git-release-history",
  "scope": "public",
  "destination": "environment/workload/projects/git-release-history",
  "repository": "https://github.com/git/git.git",
  "commit": "<full lowercase 40- or 64-hex commit ID>",
  "ref_policy": "one-detached-commit-closure",
  "object_inventory": {
    "algorithm": "sha256-canonical-git-objects-v1",
    "object_count": "<measured positive integer>",
    "bytes": "<measured positive framed-byte integer>",
    "sha256": "<measured 64-hex digest>"
  },
  "checkout_inventory_sha256": "<measured 64-hex digest>",
  "fsck_warnings": {"count": "<measured integer>", "sha256": "<sorted-warning-lines digest>"},
  "gitlinks": {"<original gitlink path>": "<original gitlink object ID>"},
  "notices": ["COPYING", "original per-file notices"]
}
```

There is deliberately no file-style top-level `bytes`/`sha256`. The declaration
must be inside the environment, must not overlap another payload declaration,
and is copied into the **public-only** build manifest. Private directory-history
payloads are not supported. HTTPS URLs must have no credentials, query, fragment
or nonstandard port; local/numeric host aliases and non-HTTPS protocols are
rejected. The declared public publisher hostname remains an operator trust
decision; this is not a general-purpose untrusted-URL proxy.

The fetch stage must provide a Git executable as well as Python. Existing
file-only guards need no Git installation. Git is resolved once from the caller's
PATH or an absolute `OPTIMBENCH_GIT` override, validated as an executable file,
and subsequently invoked by absolute path. Only tool discovery uses the caller
environment; Git commands still use the credential/config-free environment.
Staging/build-guard receipts record its absolute path, `git --version` and
executable SHA-256. These are execution provenance, not input integrity pins;
programmatic authoring may pass `receipt={}` to `materialize_git_history`.
Acquisition uses a fresh isolated
bare transfer repository, a fixed commit fetch with no tags/ref mapping or
submodules, no ambient Git config/credentials/proxies/helpers, no redirects,
and HTTPS as the only allowed network protocol. Hooks, external filter drivers, fsmonitor,
maintenance, replacement objects and global/system attributes/config are
disabled. Extra server objects may arrive, but only the selected commit's
complete reachable closure is repacked into the emitted repository.

The emitted directory is a real checkout with a detached HEAD and no refs
other than HEAD. Its `.git` has only the guard's canonical config, derived
version-2 index, object store and empty structural directories. Strict fsck
(`git fsck --full --strict --no-reflogs --no-progress`) and original object-ID validation are
mandatory. Shallow/promisor/partial-clone state, replace refs, grafts, alternate
stores, extra refs/reflogs, hooks, attributes overrides, sparse/worktree state,
unexpected config/index changes, metadata symlinks/shared files and additional
unreachable objects are rejected, not ignored or repaired.

Strict fsck errors always fail. Historic warnings require a pinned exact list:
sort the complete warning lines as bytes, append `\n` to each, and bind both the
count and SHA-256 of their concatenation in `fsck_warnings`. Missing that field
means **zero** warnings (SHA-256 of empty bytes). No warning category is globally
ignored; a missing/extra/changed warning fails even when fsck exits zero.

**Object inventory v1:** enumerate reachable full object IDs, deduplicate, sort
ASCII hexadecimal IDs lexicographically, then hash this concatenation:

`uint64be(len(id_ascii)) || id_ascii || uint64be(len(type_ascii)) || type_ascii || uint64be(len(raw)) || raw`

`raw` is the exact original unfiltered object payload from `git cat-file --batch`.
Each object ID is independently recomputed from `type + " " + decimal_size + NUL
+ raw` using the repository's Git hash algorithm. Bind the unique object count,
the total **framed** byte count, and the SHA-256 of the entire stream. Count and
byte commitments are also checked while validating. Successful fetch/fsck alone
does not authenticate the declared input.

**Checkout inventory:** recursively enumerate every original file/symlink/gitlink
outside the root `.git`. Paths are POSIX strings (filesystem UTF-8 decoding with
surrogate escaping for non-UTF-8 bytes). Hash compact JSON (`sort_keys=True`,
`separators=(",", ":")`, default ASCII escaping) mapping path names to
`{"mode": "100644|100755|120000", "bytes": <integer>, "sha256": "<digest>"}`.
File digests cover Git's original **checked-out bytes**; symlinks cover literal
target bytes. Gitlinks use `{"mode":"160000","gitlink":"<OID>","initialized":false}`
and must also be explicitly pinned in `gitlinks` (omitted means none).

Git itself checks out the canonical original index with controlled config and
umask. Original `.gitattributes` built-in text/EOL/encoding rules are respected,
including upstream CRLF files—never rewrite them to satisfy a raw-blob hash.
No hooks, configured custom filter drivers or submodule initialization/fetch run.
Verification independently reconstructs the original checkout from the canonical
index in a fresh directory; a forged checkout digest or edited working-tree
attributes cannot bless different bytes. Object payloads/IDs remain original and
are authenticated separately. Reject missing/extra files/directories, unexpected
permissions, unsafe symlinks and any initialized/nonempty gitlink directory or
submodule metadata/configuration.

For authoring, `tools/payload_check.py:materialize_git_history(url, commit, fresh_path)`
returns the object/checkout inventories plus exact warning and gitlink pins of a
fresh canonical repository. Freeze them in the declaration **before** packaging.
Guard acquisition compares all commitments before atomically publishing.
`stage_payload.py --fetch`, `--from` canonical directory packets, the build guard,
payload status, public extraction and `check_tasks` all use the same verifier.
Cached/generated packs are merely transport: they are authenticated again.
Do not modify a staged canonical repository; give consumers an independent copy.

Directory bodies must never be retained in Git. `check_tasks` rejects tracked
files under declared histories, allows only their explicitly declared nested
`.git`, and treats their source files as data rather than package glue. Catalog
budgets show the pinned framed-object bytes as upstream input, not retained Git
bytes. Provenance binds the declaration, not pack-layout-dependent file hashes.
All downloads/preparation are before timing; runtime remains network-off.
Packaging/pack-layout changes still require the candidate's full public parity
and frozen-protocol A/A qualification. This type does not create or expose any
held-out instances.

## Required tree

```text
README.md                              benchmark overview and generated task table
datasets/
  all.json                             every task
  ready.json                           authenticated, complete PUBLIC payloads
docs/
  task-format.md                       this specification
  runner.md                            executor, adapter, timing, baseline protocol
  judge.md                             admission, correctness, provisional/scored modes
  adding-a-task.md                      author checklist
tools/
  check_tasks.py                       structural checks and catalog freshness
  stage_payload.py                     generic authenticated payload staging
  update_catalog.py                    generated metadata/table/selections
  runner/                              shared execution implementation
  tests/                               tool/runner unit tests
  replay_tasks.py                       image parity and verifier controls
  payload_check.py                      shared authenticated acquisition implementation
judge/                                 verifier-only code and tests
tasks/<id>/
  task.toml                            task schema 1.4 (Harbor-era format), eight-core environment, Git pins
  task.json                            original workload and behavioral contract
  instruction.md                       objective, editable roots, prohibited changes
  README.md                            public usage and limitations
  RESEARCH.md                          optional workload research and evidence, not part of the agent image or contract
  provenance.json                      source/input provenance and current hash bindings
  release.json                         original source policy and upstream suite
  qualification-status.json            payload status and scored-qualification boundary
  payload.json                         public and held-out staging inventory
  environment/                         ENTIRE agent Docker build context
    Dockerfile                         pinned sources, asserted root commit, baseline gate
    README.md                          source/dependency preparation
    source.lock.json                   pinned upstream identity
    pristine-files.lock.json           protected source inventory
    public-workloads.lock.json         public workload provenance
    payload-public.json                generated public-only subset of payload.json
    payload_check.py                   identical build-time fail-closed guard
    notices/DISTRIBUTION.md            image-carried notice index + verbatim notices
    workload/
      runner.py                        byte-identical tools/runner/runner.py
      task.py                          ENV, cases, prepare, collect; optional reference/compare
      build.sh                         offline source-aware build + thin runner wrapper
      inputs/manifest.json             frozen public cases or locked-case descriptor
  tests/                               NEVER copied into the agent environment
    Dockerfile                         separate verifier, repository-root build context
    README.md                          verifier requirements and limitations
    test.sh                            legacy Harbor verifier entry (score with judge.score)
    judge.json                         common protocol + task-specific build/upstream settings
    heldout.lock.json                  held-out content commitments, explicit availability
    heldout/manifest.json              held-out manifest, possibly unavailable/empty
```

Optional files are `notices/` at the task root or within the environment beside
redistributed assets; protected library glue under `workload/driver/`; input
content-addressed `inputs/files/` (or the original committed direct input layout);
`inputs/data/`, `public/`, and locked-case descriptors for multi-file corpora;
source overlays, dependency locks, `prepare*.py`, and task-specific `tests/upstream.py`.
Protected single-worker affinity policy may live in `workload/worker_launcher.py`.
Verifier-only diagnostics may live in `tests/affinity_diagnostic.py`; they never
constitute scored qualification. `tests/receipts/` may retain compressed complete
qualification evidence with an index of compressed/uncompressed hashes. These
files remain outside the agent environment and must not grant claimability.
Task-level `.gitignore`/`.gitattributes` preserve original byte/mode rules.
Library glue may call the candidate API but is not an executor: it must not time,
supervise, kill, or score candidate processes. There is no per-task Python runner.
Do not rename files inside pinned upstream archives or change notice contents.

The permitted optional immediate entries are enumerated below; the checker rejects
other root entries. Nested input/source/notice trees retain their original pinned
contents, not a new per-file naming convention.

| Directory | Optional immediate entries |
| --- | --- |
| task root | `.gitignore`, `.gitattributes`, `public/`, `notices/` |
| environment | `.dockerignore`, `.gitignore`, `layout.json`, `notices/`, `png-inputs.lock.json`, `prepare.py`, `public-artifacts.json`, `public-inputs.lock.json`, `requirements-tests.lock`, `requirements-toolchain.txt`, `requirements.lock`, `source-overlay/`, `source-policy.json`, `toolchain-image.lock.json`, `toolchain-packages.lock.json` |
| workload | `baseline-outputs.json`, `cases/`, `check_public.py`, `compiler.py`, `corpus.json`, `driver/`, `fetch_inputs.py`, `frame_pipeline.c`, `guards/`, `interface_snapshot.py`, `layout.json`, `manifest.json`, `native.py`, `native/`, `notices/`, `numpy_helpers.py`, `public-inputs.lock.json`, `public-trees.json`, `worker.c`, `zrip.Cargo.lock` |
| tests | `api_guard.c`, `check_dev_outputs.py`, `notices/`, `upstream-config.json`, `upstream-expectations.json`, `upstream.py` |

Public transport descriptors use `public/README.md`, `public/manifest.json`
and `public/sources.json`, not another task-root naming scheme.

## Collection and package versions

`datasets/v1.0.json` selects a collection; it does not rename the original
packages. Its `task_version` is the default package version. A member with a
different original version must have an explicit `task_versions` override,
for example `"task_versions": {"legacy-example": "0.2.0"}`. Overrides may name
only selected members. `task.toml` and every present `task_version` field must
agree with that member's selected version. Do not add version fields or bump
an original package merely to satisfy collection membership.

Legacy-version members retain their original prompt format rather than the
v1.0 neutral prompt format.

## Payload status and one staging command

`qualification-status.json.payload_status` is the sole current payload-state field:

| Value | Meaning |
| --- | --- |
| `public-payload-missing` | A public input has neither authenticated local bytes nor a byte-verified upstream build recipe. |
| `heldout-missing` | The public Git/fetch recipe is complete, but held-out scoring assets are unavailable. |
| `ready` | Both public and held-out inventories are authenticated and complete. |

Precedence is public-missing, then heldout-missing, then ready. Historical
`status`, `heldout_status`, and the legacy `harbor_execution_qualified` field are
not payload readiness. `datasets/ready.json` means **public execution ready**, not scored
qualification; `check_tasks --require-ready` checks that same public boundary.
No task is silently promoted to scored readiness.

`payload.json` schema version 1 contains `task` and `files`. Each file records
`scope` (`public` or `heldout`), `name` (source basename), `destination`
(task-relative safe path), `bytes`, `sha256`, and optionally a pinned HTTPS `url`
and a recovery `reason`. It lists the complete required inventory; the generic
tool computes which files are missing or corrupt. A genuinely unrecovered private
manifest has explicit null size/hash and a reason. Null is not a wildcard:
staging refuses it until the original commitment is recovered and reviewed.

After placing the original bytes in a directory (by basename or task-relative
layout), run:

```sh
python3 tools/stage_payload.py --task <id> --from /path/to/payload
```

Use this fallback only for data without a reproducible upstream. It handles
delivery archives, raw RGB planes, locked multi-file corpora and ordinary content-addressed inputs. It stages atomically
per file and refuses wrong bytes and path escapes. Optional operator-side
`--fetch` performs the same pinned upstream derivation as the build. Archive
extraction or PNG conversion must produce the original final bytes; a PNG URL
is not mislabeled as a raw RGB URL. Existing acquisition evidence is historical.
Held-out staging additionally needs `--scope heldout`, recovery of authentic
manifest/reference commitments, `heldout.lock.json` availability, and requalification.
Public inputs are never substituted for held-out data.

Staging authenticates only the selected payload and does not regenerate catalogs.
After changing permitted package metadata, explicitly run
`python3 tools/update_catalog.py --task <id> --refresh-bindings`.
Global regeneration is a separate maintenance operation; `check_tasks` rejects stale generated data.
The Docker build guard and verifier report `PAYLOAD PENDING [<id>]` and the same
staging command before attempting execution. No agent image contains a private
payload manifest or operator recovery paths.

## Reproducibility and evidence

Every source root commit uses the fixed identity/date and tree/commit assertions
in [runner.md](runner.md). A package move does not authorize source pin changes,
new cases, changed expected results, broader edit policies, or dropped upstream
suites. Update current package path/hash bindings after a move; protected input
and upstream-source inventories are never refreshed from package metadata.
The deterministic branch is `optimbench-baseline`, not `main`: branch names
must not collide with source paths used by upstream test scripts. Branch naming
does not change the asserted commit or tree identity.

For public-ready tasks, record rebuilt-image LocalExecutor and DockerExecutor
parity, frozen-reference checks, upstream identity/outcome ledgers and no-op /
output-changing negative controls in the operator-selected output directory. Missing payloads
get a uniform fail-closed receipt, not invented parity evidence. A previous
qualified baseline is not proof the current shell was executed.

Execution logs, migration records and superseded qualification evidence are not
part of task packages; prior versions remain in Git history. No generic payload
store is used; recovery-only public LFS is the explicit exception above. Verbatim
public notices are also copied into `environment/notices/` and `/app/notices/`
so the published images retain them. No pull request is needed to run or
validate the benchmark.
