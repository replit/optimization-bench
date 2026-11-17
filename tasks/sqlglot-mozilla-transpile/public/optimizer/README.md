# Optimizer unit

The optimizer unit is one of the task's four equally weighted units (conversion,
optimizer, column lineage, structural diff). It runs SQLGlot's full schema-aware
optimization pipeline on 125 public requests; the held-out set has 36 requests.
Column lineage and structural diff are separate units described in
`../alternates/README.md`.

## Operation

Each unit runs in its own cold Python process against the candidate SQLGlot
build. For each request the protected child:

1. Reads the SQL, declared dialect and schema mapping.
2. Calls `parse_one(..., error_level=ErrorLevel.RAISE)`, then
   `normalize_identifiers` and `quote_identifiers`.
3. Calls `optimize` with the full pinned `RULES` sequence, `infer_schema=True`,
   `quote_identifiers=True` and `db=catalog=None`.
4. Generates the complete SQL in the request dialect with `pretty=False`.
5. Serializes the complete semantic tree (expression classes, ordered children,
   types, comments and metadata) and writes the result.

Exception class and message, SQLGlot log records and Python warnings are part of
the result. Startup, imports, input reads, all API work and output writes are
inside the measured process.

## Inputs

Public (125 requests):

- 3 queries from the SQLMesh "sushi" example project (Apache-2.0) and 1 query from
  SQLGlot's README (MIT), stored in Git as JSON requests.
- 22 TPC-H and 99 TPC-DS queries from SQLGlot's optimizer fixtures. These are
  fetch-only: they are not stored in Git or in the image. The public preflight
  downloads the pinned SQLGlot files and `environment/workload/fetch_inputs.py`
  derives the request bytes locally; `environment/public-input-packet.json`
  commits their sizes and SHA-256 values. Only the original queries are used,
  never the neighbouring expected SQL in the fixtures.

Held-out (36 requests): Mozilla bigquery-etl queries (MPL-2.0) at commit
`c5dcc248916a4eb19b1b37cb4780fa794bb1fa56`, each with the publisher's table
schemas. They ship under `tests/heldout/` (bodies in Git LFS), outside the agent
image.

Sources, licences and the changes made to each input are listed in
`environment/notices/INPUTS.md` and `tests/notices/HELDOUT.md`. Exact source URLs
and SHA-256 values are in `provenance.json`.

## Authoring tools

- `prepare.py` rebuilds and verifies the public requests and their expected
  outputs from hash-pinned sources. Run it in the task image against an unchanged
  baseline build:

  ```sh
  python tasks/sqlglot-mozilla-transpile/public/optimizer/prepare.py \
    --runtime /absolute/path/to/baseline/runtime
  ```

  Without `--freeze` it only verifies the committed bytes. `--sqlglot-source`
  and `--sqlmesh-cache` point to local copies of the pinned sources.
- `prepare_private.py` verifies the held-out requests and expected outputs from
  the hash-pinned Mozilla archive (`--runtime`, `--archive`, `--workdir`). It
  needs PyYAML from `requirements-authoring.lock`, which is not part of the task
  image.
- `test_authoring.py` contains synthetic regression tests for both tools.
- `ownership.json` and `development-aa.json` record development measurements
  (cProfile Python self time and an early timing-noise check); they are not
  scoring inputs.

`tests/check_dev_outputs.py` runs development checks against a baseline build:
complete-output comparison, rebuilt wrong-result, skipped-work and slowed-but-
correct controls, and a missing-output check.
