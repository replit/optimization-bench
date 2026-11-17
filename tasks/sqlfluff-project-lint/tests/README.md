# Reproduce and judge SQLFluff

## Requirements and data

Use Linux amd64, Docker with usable cgroup/cpuset controls, eight assigned CPU
cores, and 16 GiB available to each task container, plus host overhead. Reserve
20 GiB of free disk for images, source, builds and complete result files.
The task image pins CPython 3.14.6 and SQLFluff 4.4.0; the host judge supports
CPython 3.12 or 3.14 with the repository's hash-locked host dependencies.
No model or paid service is needed for any command below.

Start from a source packet with materialized verifier bodies, or
from a checkout with access to its declared LFS transport. For an LFS checkout:

```sh
git lfs install --local --skip-smudge
git lfs pull --include='tasks/sqlfluff-project-lint/tests/heldout/**' --exclude=''
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools
python3 tools/check_tasks.py --task sqlfluff-project-lint --require-ready
python3 -m tools.release_preflight --task sqlfluff-project-lint \
  --require-payloads --require-generic-hooks
```

An ordinary Git archive containing only LFS pointers is not a complete verifier
packet. Missing, unsmudged or altered bodies fail closed. Public image builds
do not need verifier LFS. The public source/projects are fetched anonymously
from the pinned upstream URLs during image construction; execution is offline.
Keep the release's licenses, [provenance notices](../RESEARCH.md) and the held-out
project licenses in [notices/](notices/README.md) with it.
No account, private filesystem, private image registry or host registry entry
is required for self-hosted correctness evaluation.

Only `environment/` is the agent Docker context. Keep `tests/`, the source packet
and judge results outside the agent container. Releasing evaluator data is not
permission to put expected outputs or held-out inputs in that image.

## Build, run and export a control candidate

From the repository root:

```sh
task=sqlfluff-project-lint
image=optimbench:sqlfluff-project-lint
work="$(mktemp -d)"
docker build -t "$image" "tasks/$task/environment"
docker run --rm --network none --cpuset-cpus 0-7 --memory 16g \
  "$image" /app/baseline-build/run
docker run --rm --network none --cpuset-cpus 0-7 --memory 16g \
  "$image" sh -c 'sh /app/workload/build.sh && /app/build/run'

cid="$(docker create "$image")"
docker cp "$cid:/app/sqlfluff/src/sqlfluff" "$work/sqlfluff"
docker rm "$cid"
python3 - "$work" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
(root / "artifacts.json").write_text(json.dumps({"src/sqlfluff": str(root / "sqlfluff")}))
PY
```

This is the real declared source-export interface, not an installed-wheel
submission. To test a candidate, apply its allowed Python source edits to that
export while retaining the protected original files. The judge reconstructs
the complete pinned project and builds it offline. The unchanged export above
is the no-op control; do not regenerate goldens to accommodate a failure.

## Public gates and held-out controls

Use a fresh output directory or filename each time. `--separate-sandbox`
attests that evaluation is isolated from an optimization-agent environment;
run on a genuinely separate evaluation sandbox when an agent trial was used.

```sh
python3 -m judge.score --task "$task" --artifacts "$work/artifacts.json" \
  --image "$image" --cpuset 0-7 --cache "$work/cache" \
  --gates-only --separate-sandbox --out "$work/public-gates"
python3 -m judge.heldout_check --task "tasks/$task" \
  --image "$image" --cpuset 0-7 --cache "$work/cache" \
  --artifacts "$work/artifacts.json" --output "$work/heldout-noop.json"
negative_status=0
python3 -m judge.heldout_check --task "tasks/$task" \
  --image "$image" --cpuset 0-7 --cache "$work/cache" \
  --negative-control --output "$work/heldout-negative.json" || negative_status=$?
test "$negative_status" -eq 1
```

The negative control must report `negative control rejected` with
`control_passed: true`; its deliberate mismatch exits 1. Exit 2 or an invalid
run is not successful rejection. Read `result.json`, `summary.txt`, retained
gate receipts and per-case records, not only command exit codes.

The pristine reference and candidate must preserve all 14,047 upstream test
identities: 11,417 passed, 2,630 skipped and zero failed, including the nine
protected auto-fix cases. Source/interface admission, real public Python API
fault/repeat/aliasing callbacks, CLI OS-fault controls, process state and actual
1/2/4/8-CPU correctness evidence remain mandatory. Header exclusion is supported
only by the pinned pure-Python package's zero-header inventory. A registration
inventory is not evidence these native processes executed successfully.

All four public and all four held-out units remain equal-weight: configured
three-project lint, serial PostgreSQL auto-fix, Snowflake/Jinja lint, and complete
BigQuery syntax export. The original lint's eight workers, new units' one-worker
settings, process-exit boundaries, complete streams/status/file effects and
positioned tree serialization are unchanged. Metadata preparation is untimed.
Additional generated differential and fault cases are correctness-only.

## Self-hosted timing versus certification

For an isolated self-hosted provisional evaluation, use the same `judge.score`
command without `--gates-only`. It retains the frozen minimum of five pairs,
maximum of fifteen, one warmup, 2% interval target, output checks and A/A/host
quality rules. No local host whitelist or reduced-duration exception is needed
or permitted. Failed pristine admission or mandatory gates remain failures.

Results from machines that do not match the reference scoring configuration
are provisional and not claimable.
