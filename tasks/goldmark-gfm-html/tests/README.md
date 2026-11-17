# Goldmark release reproduction

This is the original pinned Goldmark workload, not a new benchmark. The two
performance units are `gfm-html` and `parse-only`. Each processes the same
10,392 public documents (116,306,743 input bytes); the protected scope uses
12,581 documents (122,436,684 input bytes) per unit. Every complete output and
error file is checked. The documents are not independent timing units.

The Go batch process constructs its parser/renderer once, then parses each
document with a fresh context, AST, and output buffer. Keep that process model,
document order, parser options, exact-byte checks, and timing boundary.
`release.json`, `environment/source.lock.json`, the public manifests, and
`heldout.lock.json` are authoritative. Do not regenerate expected outputs.

## Corpus inventory

A fresh inventory of the frozen manifests finds 33 named public repositories
and 13 protected repositories, with no shared repository name. All 10,392
public input hashes are distinct, all 12,581 protected input hashes are
distinct, and no input SHA-256 occurs in both scopes. Both performance units
use the same documents within a scope; that intentional reuse is not an
expansion of the timing sample count. All protected documents retain their
original repository/commit/blob provenance. Public legacy snapshots additionally
use the authenticated transport records and original third-party notices.

## Obtain a complete release

A release distributor must provide a **hydrated** source export: ordinary
files plus every Git LFS object required by this task. A Git archive containing
LFS pointer text is not a usable release. Nothing below requires an account,
an image registry login, or a registered scoring host once the hydrated source
has been supplied.

From a Git checkout, materialize just this task before exporting:

```sh
git lfs install --local
git lfs pull --include='tasks/goldmark-gfm-html/**' --exclude=''
```

All public upstream acquisitions use pinned credential-free HTTPS records.
From the release root, stage them before building:

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PYTHONPATH=.:tools
PYTHON=.venv/bin/python
"$PYTHON" tools/stage_payload.py --task goldmark-gfm-html --scope public --fetch
docker build -t optimbench:goldmark-gfm-html tasks/goldmark-gfm-html/environment
docker image inspect --format '{{.Id}}' optimbench:goldmark-gfm-html
```

The build authenticates source and dependency bytes, checks the deterministic
source commit/tree, builds both native drivers, and checks all 20,784 public
results. Its build context contains only `environment/`; protected inputs,
expected outputs, and held-out notices stay outside the agent image.
Public notices are in `../notices/` and `../environment/notices/`. Protected
source attributions and verbatim license texts are in [notices/](notices/).

Requirements: Linux/amd64, Docker with working resource limits, Python 3.12 or
3.14 for the host judge, and Git LFS only when obtaining a checkout rather than
a hydrated export. Provisional scoring requires eight actual logical CPUs and
16 GiB of container memory. The full 1/2/4/8-CPU correctness matrix is mandatory;
do not simulate CPUs or relax affinity checks on a smaller machine.

## Export an unchanged control, or apply a candidate

Extract the pinned source from the newly built image. This source is not
obtained from an agent log or a cached candidate:

```sh
IMAGE=optimbench:goldmark-gfm-html
CONTAINER=$(docker create "$IMAGE")
docker cp "$CONTAINER":/app/goldmark ./goldmark-control
docker rm "$CONTAINER"
cp -a goldmark-control goldmark-candidate
: > unchanged.patch
git -C goldmark-candidate apply --allow-empty ../unchanged.patch
```

For a real submission, replace `unchanged.patch` with a patch to the permitted
production Go files. The six exported roots are `ast`, `parser`, `renderer`,
`text`, `util`, and `extension`; protected source/tests must remain byte-exact.
The complete modify-only allowlist is in `../release.json`. This command writes
the same root-map format for an unchanged or modified candidate:

```sh
"$PYTHON" - <<'PY'
import json
from pathlib import Path
source = Path("goldmark-candidate").resolve()
roots = ("ast", "parser", "renderer", "text", "util", "extension")
Path("goldmark-artifacts.json").write_text(
    json.dumps({name: str(source / name) for name in roots}, indent=2) + "\n")
PY
```

A task-native public check can run without a scoring registry or held-out data:

```sh
docker run --rm --network=none \
  -v "$PWD/goldmark-candidate:/submitted:ro" \
  -e OPTIMBENCH_SOURCE=/submitted "$IMAGE" sh -eu -c '
    OUT=$(mktemp -d)
    sh /app/workload/build.sh "$OUT/build"
    "$OUT/build/run" > "$OUT/results.tsv"
    python3 /app/workload/check_public.py "$OUT/results.tsv"
  '
```

Every invocation must use a fresh build/output directory. Compare fingerprints
by `(path, bytes, SHA-256)`, not traversal order. `set -eu` prevents a failed
build or partial run from accidentally checking a stale result file. Upstream
tests remain `cd /app/goldmark && make test`; the optional upstream lint target
requires tools absent from the ordinary pinned task image.

## Protected and generic verifier checks

Choose eight CPUs actually available to Docker; `0-7` is an example, not a
portable affinity claim. The following is correctness-only, with no timing,
calibration, model run, registered host, or speedup claim:

```sh
CPUSET=0-7
"$PYTHON" -m judge.heldout_check \
  --task tasks/goldmark-gfm-html --image "$IMAGE" --cpuset "$CPUSET" \
  --artifacts goldmark-artifacts.json --output goldmark-heldout.json
"$PYTHON" -m judge.heldout_check \
  --task tasks/goldmark-gfm-html --image "$IMAGE" --cpuset "$CPUSET" \
  --negative-control --output goldmark-heldout-negative.json
```

The unchanged candidate must pass both units' 25,162 protected output checks
after authenticated rebuild and original upstream tests. The verifier-owned
negative control appends one byte to one observed output; it must report
`negative control rejected`, `control_passed: true`, and exit 1. It never
changes a fixture, expected output, or candidate source.

On a judge machine genuinely separate from the machine that ran a candidate
agent, use the public scorer's native gate entry point:

```sh
"$PYTHON" -m judge.score --task goldmark-gfm-html \
  --artifacts goldmark-artifacts.json --image "$IMAGE" \
  --mode provisional --separate-sandbox --gates-only --out goldmark-gates
```

`--separate-sandbox` is an operator attestation, not a way to conceal a shared
agent/judge host. The command runs admission, public interface, process-state,
upstream, differential, writer-fault, repeat, alias, and physical-CPU subset
checks. See [fuzz/rc-registration.md](fuzz/rc-registration.md) for bounded
coverage and conditional exclusions. Remove `--gates-only` only when intending
to run the shared paired timing protocol. Registry availability can affect
claimability, never whether mismatched outputs or pending gates are accepted.

Use fresh output directories and retain the source revision, task definition
hashes, immutable Docker image ID, commands, exit codes, and JSON results.
Membership, transport preflight, fixture regression tests, and old receipts
are not current native execution or performance qualification.
