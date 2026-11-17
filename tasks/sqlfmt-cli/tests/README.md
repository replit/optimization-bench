# Public sqlfmt reproduction

This task has two mandatory, equally weighted timed units:

| Unit | Public | Held-out |
| --- | ---: | ---: |
| `batch` | 89 cold CLI invocations | 302 cold CLI invocations |
| `native-batch` | 844 documents in one cold native call | 2,470 documents in one cold native call |

Documents are not individual scoring weights. Both entry points are rebuilt
offline from the same exported `format/` and `cmd/sqlfmt/` source roots. The
protected native caller uses `format.Format(io.Reader)`. Complete outputs,
errors, status and cleanup are checked; the native unit is not diagnostic.
Original input bytes, expected commitments and the shared whole-process clock
are unchanged. No padding or candidate-dependent filtering is permitted.

## Host and data preparation

Use Linux x86-64, Docker with BuildKit enabled, Git LFS, and final CPython
3.12 or 3.14. BuildKit is required for the Dockerfile's `RUN --mount` instructions.
Execution needs eight actual available CPUs and a 16-GiB container limit, plus
host overhead.
Use CPUs 0–7 below only when available. Do not overlap native/judge/timing
commands with other measured jobs. The image pins Go 1.26.9, its base-image
digests, original source archive and module checksums. No workspace credential,
private registry, optimization agent, or model API is needed.

The public release repository serves this task's Git LFS objects anonymously,
so no account is needed; a hydrated source archive works as well. The
following commands require a source checkout.

From the repository root:

```sh
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools

python3 tools/stage_payload.py --task sqlfmt-cli --fetch
python3 tools/check_tasks.py --task sqlfmt-cli --require-ready
recipe=$(python3 -c 'from pathlib import Path; from judge.image_recipe import descriptor; print(descriptor(Path("tasks/sqlfmt-cli"))["recipe_sha256"])')
docker build --provenance=false --network=host --progress=plain \
  --label "org.optimbench.recipe-sha256=$recipe" -t optimbench:sqlfmt-cli \
  tasks/sqlfmt-cli/environment
image=$(docker image inspect --format '{{.Id}}' optimbench:sqlfmt-cli)
python3 -m tools.public_runtime_packet --task sqlfmt-cli --image "$image" \
  --cache /tmp/sqlfmt-public-cache --out /tmp/sqlfmt-agent-packet
```

Public bodies are authenticated before the build and exposed through transient
read-only build mounts, never copied into image layers. Build-time networking
acquires the pinned implementation and module dependencies; execution uses
`--network none`. The build checks all 180 public stdout/stderr commitments.
The explicit host-network build option handles hosts with broken bridge HTTPS;
it grants no measured-runtime network access. The runtime packet authenticates
the same 933 bodies and mounts only their declared root read-only. Its generated
agent command only starts an agent; it is not correctness or performance qualification.

There are 933 public input bodies plus 8 pinned CLI license/notice documents.
All bodies are fetch-only. `payload.json` and the CLI manifest bind immutable
upstream commits, source/member SHA-256, sizes and exact framing; the standalone
build guard verifies final hashes. Native archive pins and per-file origins are
in [native-corpus.json](fuzz/native/native-corpus.json). Acquisition is outside
the measured interval. A missing or changed upstream fails closed.

### Redistribution is not implied by public access

The ten Sourcegraph public-snapshot CLI inputs (Sourcegraph Enterprise License,
which does not grant general redistribution) were removed on 2026-11-15. The
sixteen TimescaleDB validation inputs from its `tsl/` directory (source-available
Timescale License) and that license's notice were removed on 2026-11-17. CLI
sources include AGPL-3.0 Citus, PostgreSQL-licensed pg_partman/pgvector,
Apache-2.0-licensed TimescaleDB (`sql/` and `test/sql/` only), Apache-licensed
Supabase and GPL/file-specific PostGIS material. Each CLI input row in
`payload.json` records its SPDX license (`AGPL-3.0-only`, `PostgreSQL`,
`Apache-2.0` or `GPL-2.0-or-later`), read from the source's license file at the
pinned commit. One PostGIS case also contains the three
`extras/ogc_test_suite/` files. They carry a 1998 Open GIS Consortium
copyright and refer to a separate OGC license that PostGIS does not include, so
that row is labelled `GPL-2.0-or-later AND LicenseRef-OGC-Conformance-Test-Suite-1998`;
like every public CLI input it is fetch-only. Original license/notice bytes are
fetched with SHA-256 pins into the local image's `/app/notices/`; the PostGIS
notices for the same commit are retained in `environment/notices/postgis-native-*`.
Original source headers and attribution are retained. Read the specific source
terms, not this summary.

The source release therefore contains acquisition recipes, not those SQL
bodies. Do not add acquired `environment/workload/inputs/files/` bytes to Git
or a distributed source export. Historical input-bearing images and diagnostic
artifacts must not be rehosted. The task image is published on Public ECR as
`optimbench:sqlfmt-cli-1.0.0` and pinned by digest in `task.toml`. Only that
final body-free image is published, after an audit of every image layer and its
metadata; a clean final filesystem alone is insufficient, and build stages,
caches and historical input-bearing images are never published. Any rebuild
must pass the same audit before it replaces the published image. The
task-owned [layer audit](fuzz/image_layers.py) and its copy-then-delete negative
control detect complete original input files and an audit canary, not arbitrary
encoded data or every possible license/disclosure concern. A hydrated export
includes the held-out LFS assets but must still exclude fetch-only public inputs.

### Held-out assets

The separate verifier requires all 3,381 held-out file commitments, not agent
access to them. Held-out assets are public release material. Fetch only this
Task's objects with a scoped Git LFS pull:

```sh
git lfs install --local --skip-smudge
git lfs pull \
  --include='tasks/sqlfmt-cli/tests/heldout/**,tasks/sqlfmt-cli/tests/fuzz/native/native-public-reference.bin' \
  --exclude=''
python3 -c 'from judge.heldout import check; check("tasks/sqlfmt-cli/tests", require_available=True)'
```

A hydrated release archive may supply the same authenticated LFS object bytes
instead. The public release repository serves these objects anonymously, so
the scoped pull above needs no account. An LFS pointer is not data.
Inability to obtain permitted bytes is a release blocker, not a reason to skip
judging or replace expected outputs.
Keep `tests/` outside the agent's Docker build context and source exports.

CLI held-out sources are Chinook
`7f67772503d71ba90f19283c38e93923addb43fa` (MIT) and PostgreSQL
`6304632eaa2107bb1763d29e213ff166ff6104c0` (PostgreSQL License), with archive
SHA-256 pins in `provenance.json`. Their verbatim notices are in
`tests/fuzz/notices/`. Native held-out Mozilla bigquery-etl revisions, MPL-2.0
notices and full origin/member hashes are in `tests/fuzz/native/` and
`tests/heldout/native-origins.json`. The PostGIS OGC suite is excluded from
both native batches.

The public native golden `fuzz/native/native-public-reference.bin`
(25,993,649 bytes, fetched by the scoped LFS pull above) and the held-out
references are sqlfmt output derived from PostgreSQL (PostgreSQL License),
PostGIS (GPL-2.0-or-later, with file-specific MIT and documentation terms) and
Mozilla bigquery-etl (MPL-2.0) sources. Their derivation, modification
statement and license terms are in
[NATIVE-REFERENCE-OUTPUTS.md](fuzz/native/notices/NATIVE-REFERENCE-OUTPUTS.md).

## Source-only candidate and admission

For an unchanged control, export exactly the permitted roots from the built
image. For a real candidate, edit only those roots in your agent container and
export them the same way; the verifier never accepts its prebuilt executable.

```sh
mkdir -p /tmp/sqlfmt-export
container=$(docker create "$image")
docker cp "$container:/app/sqlfmt/format" /tmp/sqlfmt-export/format
docker cp "$container:/app/sqlfmt/cmd/sqlfmt" /tmp/sqlfmt-export/cmd-sqlfmt
docker rm "$container"
printf '{"format":"/tmp/sqlfmt-export/format","cmd/sqlfmt":"/tmp/sqlfmt-export/cmd-sqlfmt"}\n' \
  > /tmp/sqlfmt-export/artifacts.json

python3 -m judge.score --task sqlfmt-cli \
  --artifacts /tmp/sqlfmt-export/artifacts.json --mode provisional \
  --separate-sandbox --gates-only --image "$image" --cpuset 0-7 \
  --cache /tmp/sqlfmt-cache --out /tmp/sqlfmt-public-gates

python3 -m judge.heldout_check --task tasks/sqlfmt-cli \
  --artifacts /tmp/sqlfmt-export/artifacts.json --image "$image" --cpuset 0-7 \
  --cache /tmp/sqlfmt-cache --output /tmp/sqlfmt-heldout.json
```

The first command runs public admission, interface, differential, upstream,
process-state, generic contracts and CPU-subset gates without sampling. The
second authenticates held-out data, admits and reconstructs the export, runs
upstream tests, and compares every held-out CLI/native output. It is untimed
correctness evidence, not a speedup.

Run this verifier-owned negative control separately; exit status **1** is
expected, with `control_passed: true` and `status: negative control rejected`:

```sh
python3 -m judge.heldout_check --negative-control \
  --task tasks/sqlfmt-cli --image "$image" --cpuset 0-7 \
  --cache /tmp/sqlfmt-cache --output /tmp/sqlfmt-heldout-negative.json
```

Real source-level wrong, omitted-output and slower controls, caller compilation
and unit tests are documented in [the native guide](fuzz/native/README.md).
The controls do not generate or update any frozen expected output.

## Provisional scoring

After both admissions pass and held-out assets authenticate, omit
`--gates-only` from the public-gate command and use a fresh output directory.
This exercises the complete default provisional scorer with both weighted
units, held-out judging and unchanged sampling policy. Preserve the standard
8-CPU/16-GiB limits, 1/2/4/8-CPU correctness matrix, RMIT/A/A checks,
upstream/generic gates and 5–15-pair sampling settings. Do not substitute
`legacy`, reduce samples or change thresholds to obtain a passing report.

An unregistered development host produces diagnostic, non-claimable evidence.
Neither correctness controls, a successful no-op, historical receipts nor this
public packaging qualify an official performance claim. No model or official
optimization campaign is run by these instructions.
