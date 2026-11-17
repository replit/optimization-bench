# Reproduce the bzip2 task

The agent receives only `environment/`; this directory, held-out inputs and the
shared judge stay in a separate trusted checkout. No optimization/model run is
needed to reproduce the unchanged-source control below.

## Requirements and distribution

Use Linux x86-64 with Docker, at least eight available logical CPUs, 16 GiB for
each workload container, and space for the source checkout, Docker image and
complete result files. The host judge supports final CPython 3.12 or 3.14.
The image pins Python 3.12, the base-image digest and bzip2 1.0.8's complete
source archive. Debian compiler/build packages are resolved at image build and
recorded in `/app/os-packages.lock`; retain the immutable image ID with results.
GCC, G++, libc development files, Make, Git and curl are original build
dependencies, not the later optional profiling bundle.

A source release must include the materialized LFS objects, or provide an
anonymously accessible LFS remote. A hydrated source archive needs no account,
connector, private registry or private filesystem. Build-time access to the public base image, Debian
mirrors and `https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz` is required.
Measured containers run without network access.

For a Git checkout with access to its LFS remote, recover only this task:

```sh
git lfs install --local --skip-smudge
git lfs pull --include='tasks/bzip2-codec/public/files/**,tasks/bzip2-codec/tests/heldout/**' --exclude=''
git lfs checkout 'tasks/bzip2-codec/public/files/**' 'tasks/bzip2-codec/tests/heldout/**'
python3 tools/stage_payload.py --task bzip2-codec
python3 tools/check_tasks.py --task bzip2-codec --require-ready
```

For a hydrated release archive, skip the Git LFS commands, but run the same
staging and authentication commands. `payload.json` preserves the exact bodies,
sizes and SHA-256s; unavailable or altered data fails closed.

The public packet materializes 30,606,044 logical bytes. Held-out bodies and
complete expected outputs are independently authenticated by
`heldout.lock.json`. They remain under `tests/`, never the agent image.
The held-out set is explicitly **recovered-requalified (not proven
original)**; `heldout/requalification.json` records how it was rebuilt and
requalified.

## Build and exercise every public case

From the repository root:

```sh
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools

docker build -t optimbench:bzip2-codec tasks/bzip2-codec/environment
image=$(docker image inspect optimbench:bzip2-codec --format '{{.Id}}')
docker run --rm --network none --cpuset-cpus=0-7 --memory=16g \
  "$image" /app/baseline-build/run
```

Replace `0-7` consistently with eight logical CPUs actually available to the
Docker host. Do not reduce the required CPU count to accommodate a smaller VM.
The image-build gate authenticates all 424 complete public results;
`/app/baseline-build/run` replays and prints their fingerprints from 255 cases:
253 original individual/correctness cases and the two native batches. It
reports no evaluation timing.

The two equally weighted public timed units are `distinct-natural-workload`
(96 codec operations, each once) and `small-memory-text` (75 complete block-9
streams decoded with `small=1`, each once). Held-out uses the corresponding
100-operation and 65-text batches, plus all 363 individual/correctness cases.
The original 64 KiB chunks, work factor, fresh codec state, CRCs, error/stream
consumption, allocation-cap guard and byte-exact encoded/decoded results remain
unchanged. Startup, file I/O, allocation, finalization and flushed output remain
inside the native process-start-to-exit clock. Authentication and input staging
remain outside it.

## Apply and export a candidate

Extract the pinned source and build an explicit map of the eight admitted
files. This is the real source-only submission path, not an image/binary export:

```sh
work=$(mktemp -d)
export work
container=$(docker create "$image")
docker cp "$container:/app/bzip2" "$work/source"
docker rm "$container"

# Optional: apply an authorized source-only patch to this independent tree.
# git -C "$work/source" apply /absolute/path/to/candidate.patch

python3 - <<'PY'
import json, os
from pathlib import Path
work = Path(os.environ["work"])
rules = json.loads(Path("tasks/bzip2-codec/release.json").read_text())["source_policy"]
mapping = {name: str(work / "source" / name) for name in rules["allowed_files"]}
(work / "artifacts.json").write_text(json.dumps(mapping, indent=2) + "\n")
PY
```

With no patch this exports the unchanged-source control. Only the eight
existing `.c`/`.h` files listed in `release.json` are admitted; public headers,
Makefile, driver, tests, flags and dependencies remain protected. For an
interactive agent environment, mount only a separate copy of these source
files into the task image; never mount this checkout, verifier or Docker socket.

## Public gates and complete held-out correctness

Use fresh output directories. These commands need no reviewed-host registry:

```sh
python3 -m judge.score --task bzip2-codec \
  --artifacts "$work/artifacts.json" --image "$image" \
  --mode provisional --separate-sandbox --cpuset 0-7 \
  --cache "$work/cache" --gates-only --out "$work/public-gates"

python3 -m judge.heldout_check --task tasks/bzip2-codec \
  --artifacts "$work/artifacts.json" --image "$image" --cpuset 0-7 \
  --cache "$work/cache" --output "$work/heldout.json"

python3 -m judge.heldout_check --task tasks/bzip2-codec \
  --image "$image" --cpuset 0-7 --negative-control \
  --cache "$work/cache" --output "$work/heldout-negative.json"
```

`--separate-sandbox` attests that no optimizing agent is running in the judging
sandbox. For this unchanged control no optimization agent runs at all. For a
real submission, use a separate kernel from the agent or supply its fingerprint;
do not falsely attest isolation.

The first command performs clean source/build/interface admission, original
upstream tests, differential/process-state checks and real registered native
fault, repeated-call, alias, CLI and C/C++ header consumers, including the
required CPU-subset correctness matrix. The second rebuilds the exported
candidate and checks all 375 held-out cases against complete frozen outputs.
The negative control deliberately perturbs one verifier-owned observed output,
never a fixture or golden result: exit 1 and `control_passed: true` with
`candidate_verdict: null` are the expected rejection.

Inspect `result.json`, `summary.txt`, `upstream.json`, `generic-gates/` and
`cpu-correctness/`, plus the two held-out JSON reports. Pending/unavailable
gates are not passes. None of these untimed commands claims a performance score.

For self-hosted diagnostic timing, omit `--gates-only` from the first command,
use a new `--out` directory, and leave the task's sampling settings unchanged.
The shared judge retains five-to-fifteen pairs, one warmup, the 2% relative
half-width, A/A lanes, minimum-duration/startup-share and host-quality rules.
Unregistered hosts remain provisional and `claimable: false`; a noisy or
unqualified host does not become an official score.

## Native regression controls

The image supplies both C and C++ compilers. Host-only header regression tests
also require GCC/G++ and Make:

```sh
BZIP2_HEADER_SOURCE="$work/source" \
BZIP2_HEADER_RECEIPTS="$work/header-receipts" \
  python3 -m unittest discover -s tasks/bzip2-codec/tests/fuzz -p 'test_*.py' -v
python3 -m unittest tools.tests.test_bzip_upstream_ledger \
  judge.tests.test_rc2_batch_mapping
```

The native header test authenticates every pristine file, runs real two-TU C
and C++ programs, rejects a linkage-breaking native mutant and treats a missing
C++ compiler as infrastructure failure. The original upstream ledger preserves
all six sample encode/decode checks, fifty API semantic identities and allocator
callback ownership; variable allocator invocation counts are observations,
not rewritten test identities.

## Attribution

Retain the source archive's bzip2 license, public
[`ATTRIBUTION.md`](../public/dev/ATTRIBUTION.md), all public
[`notices`](../environment/notices/), complete Gutenberg text notices and
[`PROJECT-GUTENBERG-TERMS.txt`](../environment/notices/PROJECT-GUTENBERG-TERMS.txt).
The image ships the public attribution, notice index and upstream notices under
`/app/notices/public/dev/`, including the Open Government Licence – Canada and
BIDMC (Open Data Commons Attribution License v1.0,
https://opendatacommons.org/licenses/by/1-0/) attributions.

The per-record `license` text in `environment/workload/driver/small-text.json`
(75 public texts) and `heldout/small-text-sources.json` (65 validation texts)
says the Gutenberg public-domain statement applies to the United States and
other jurisdictions require review. Those files are hash-pinned and unchanged;
[`GUTENBERG-PUBLIC-DOMAIN.md`](../environment/notices/GUTENBERG-PUBLIC-DOMAIN.md)
supersedes that text. Checked on 2026-11-17: every credited contributor of all
140 texts died in 1946 or earlier (latest: H. G. Wells, 1866-1946), so the
texts are public domain in the United States and in every country with a
copyright term of life plus 70 years or shorter.

The held-out corpus attribution, complete source/member manifest, notice index
and verbatim Newsboat MIT, Tabulator MIT, BIDMC, Canada and government notices
are under [`fuzz/notices/heldout-corpus/`](fuzz/notices/heldout-corpus/), outside
the agent context. The manifest was originally recovered byte-for-byte with
SHA-256 `74208a1da4f3d4ac4b6b0f766b6744573e655ac56617927c09f94c70badef88a`
(the `source_manifest_sha256` in `heldout/requalification.json`). On 2026-11-17
its licence-status labels were updated from "review pending" to the reviewed
terms, and its notice paths were pointed at the retained `notices/` copies; no
corpus body, size or SHA-256 changed. Its current SHA-256 is
`5d464a9b4645889098ae7d55934cd3115f826458791496a519783050a4627f97`, bound in the
task provenance. The public corpus manifest received the same 2026-11-17
relabel, and its NOAA README reference now points to the upstream URL; its
current SHA-256 is the `corpus_manifest_sha256` in
[`public/workload.json`](../public/workload.json),
`dd42cc2150050b98aba87a3b7e8bf0690be0ce4de3b7436269b8b2e543566f90`.
A separate [`Newsboat review`](fuzz/notices/heldout-corpus/NEWSBOAT-LICENSE-REVIEW.md)
authenticates all eleven source members against the anonymous upstream archive
and retains Coco/R's generated-code exception in addition to the MIT notice.

Held-out bodies and complete expected outputs are distributed with Git LFS under
`tests/heldout/`, outside the agent image. Redistribute them only together with
this attribution and notice bundle.
