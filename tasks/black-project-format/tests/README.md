# Reproduce the Black task

Run these commands from the repository root. The worker image, editable
submission, verifier and held-out inputs are separate. Do not copy `tests/`,
`judge/` or held-out data into an agent image. `test.sh` is the verifier
container entry point; `tests/Dockerfile` uses the pinned task image.

## Workload and data

There are **four public timed units**, with equal weight:

| Unit | Public case | Held-out case |
| --- | --- | --- |
| Project reformat | `django-reformat` | `faker-reformat` |
| Project check | `sqlalchemy-check` | `gcp-retail-check` |
| `warm-snippets` | `snippets-public` | `snippets-heldout` |
| `editor-ranges` | `ranges-public` | `ranges-heldout` |

`startup-empty` is a fifth public execution case but is **correctness-only**.
It is never an extra timing unit. The public manifest has seven output
documents because each public API case spans two input documents; the
held-out manifest has four. Splitting documents does not reweight the units.
The startup-share limit remains 2%; the sampling, duration and noise
requirements in `judge.json` and the shared judge are unchanged.

The image build anonymously downloads the locked Black source, SQLAlchemy
and Django archives and authenticates their sizes, archive SHA-256 hashes
and extracted inventories. The eight small public input bodies are
already in Git. No network is available during a candidate build or workload.

The release packet must also carry the Git LFS objects under this task's
`tests/heldout/` directory; a pointer-only archive is not a complete packet.
The publisher may instead provide a fully hydrated checkout or archive.
From a release checkout with its LFS objects available:

```sh
git lfs install --local
git lfs pull --include='tasks/black-project-format/tests/heldout/**' --exclude=''
python tools/check_tasks.py --task black-project-format
python -m tools.release_preflight --task black-project-format \
  --require-generic-hooks --require-payloads
```

These commands use the release checkout's own origin and LFS store; the
public release repository serves the LFS objects anonymously. No account,
service or dataset outside the release is needed once the packet is
hydrated. All 1,914 held-out files, including expected output bytes, are
authenticated by `heldout.lock.json`.

Retain the public notices under `environment/notices/`, the full held-out
project licenses, and the pinned API/range licenses under
[`fuzz/notices/heldout/`](fuzz/notices/heldout/). These held-out notices stay outside
the agent build context. [`fuzz/notices/README.md`](fuzz/notices/README.md)
records the verified SPDX license of every held-out fragment source and
supersedes the historical `NOASSERTION` and pending-sign-off wording in the
frozen `heldout/sources.json`.

## Host and image

Use Linux, Docker with cgroup accounting and at least eight available logical
CPUs and 16 GiB memory. The judge requires eight real, available CPU IDs;
replace `0-7` if the host's affinity uses different IDs. A four-CPU build or
diagnostic run is not eight-CPU judge qualification.

Install the hash-locked host dependencies; Python 3.12 and 3.14 are covered
by repository CI:

```sh
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools
TASK=tasks/black-project-format
IMAGE=optimbench:black-project-format
WORK=$(mktemp -d /tmp/optimbench-black.XXXXXX)
export WORK
RECIPE=$(python -c 'from pathlib import Path; from judge.image_recipe import descriptor; print(descriptor(Path("tasks/black-project-format"))["recipe_sha256"])')
docker build --provenance=false --progress=plain --label "org.optimbench.recipe-sha256=$RECIPE" \
  -t "$IMAGE" "$TASK/environment"
docker run --rm --network none "$IMAGE" /app/baseline-build/run
```

This is the ordinary pinned mypyc/native recipe, not a profiler image.
Image construction performs the native build and checks every public output
length and SHA-256. Subsequent source builds use the same offline
`workload/build.sh`; bytecode compilation or a pure-Python fallback is not
an acceptable replacement.

## Source or patch submission

Extract a pristine source tree from the built image. A source submission
changes only the three editable Python roots; for a patch submission, apply
the patch to this candidate copy before exporting those roots.

```sh
container=$(docker create "$IMAGE")
docker cp "$container:/app/black" "$WORK/pristine"
docker rm "$container"
cp -a "$WORK/pristine" "$WORK/candidate"
# Optional: git -C "$WORK/candidate" apply /absolute/path/to/candidate.patch
python - <<'PY'
import json
import os
from pathlib import Path

work = Path(os.environ["WORK"]).resolve()
roots = ("src/black", "src/blib2to3", "src/blackd")
(work / "submission.json").write_text(json.dumps(
    {root: str(work / "candidate" / root) for root in roots}, indent=2) + "\n")
PY
```

An unchanged candidate is the no-op control. `task.toml` declares the same
three source exports. No prebuilt candidate binaries are trusted.

## Judge and controls

Use a judge machine isolated from any running optimization agent. This
command admits the source exports, rebuilds both the pristine reference and
candidate, checks interfaces and behavior, runs native upstream tests and
the separate pure-Python `incompatible_with_mypyc` subset, runs the generic
gates and affinity-verified 1/2/4/8-CPU correctness, and evaluates the original
public and held-out timing units:

```sh
python -m judge.score --task black-project-format --mode provisional \
  --artifacts "$WORK/submission.json" --image "$IMAGE" --cpuset 0-7 \
  --separate-sandbox --cache "$WORK/cache" --out "$WORK/score"
```

Use `--gates-only` for untimed public admission/generic-gate diagnostics;
that does not exercise held-out scoring. Keep the task's sampling defaults
for an actual provisional score. Do not substitute the image's installed
baseline for the judge's independently rebuilt reference.

The following separate correctness-only controls authenticate all held-out
inputs and compare complete outputs without timing or calibration:

```sh
python -m judge.heldout_check --task "$TASK" --image "$IMAGE" --cpuset 0-7 \
  --artifacts "$WORK/submission.json" --cache "$WORK/cache" \
  --output "$WORK/heldout-positive.json"

# Exit 1 is expected only when the recorded status is "negative control rejected".
python -m judge.heldout_check --task "$TASK" --image "$IMAGE" --cpuset 0-7 \
  --negative-control --cache "$WORK/cache" \
  --output "$WORK/heldout-negative.json"
```

The negative control appends one NUL to one observed output only after the
pristine result has matched; it never edits a fixture or expected output.
Require `control_passed: true`, no failed baseline case, and precisely the
declared control case failing. A harness error or nonzero exit alone is not
a passing control.

Retain `result.json`, the public/held-out verifier records, source/build/image
bindings, both upstream identity/outcome ledgers, generic and CPU-correctness
receipts, and both held-out control JSON files. Inspect statuses and gate
results, not just command exit codes. Provisional results are not official
benchmark qualification; historical receipts and prior image IDs do not
qualify this restored task.
