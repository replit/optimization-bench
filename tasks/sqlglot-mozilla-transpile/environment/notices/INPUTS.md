# Workload inputs: sources, licences and changes

This file covers the public workload inputs of `sqlglot-mozilla-transpile`. The
licence texts it names are in this directory. SQLGlot itself (MIT,
`LICENSE.sqlglot`) is the software under test and keeps its own notices in the
pinned source tree.

## Mozilla bigquery-etl SQL (conversion unit, 284 files)

- Source: Mozilla bigquery-etl, https://github.com/mozilla/bigquery-etl, commit
  `1819e636d900f935b9b464b19b00d05aa207d210`.
- Licence: Mozilla Public License 2.0 (MPL-2.0); full text in `Mozilla-MPL-2.0`
  (also shipped as `public/dev/LICENSE.mozilla`).
- Changes: none. The 284 `query.sql` files are distributed byte-for-byte as
  published. Only the file names differ: each file is stored under a short hashed
  name, and `public/dev/manifest.json` records its original repository path and
  SHA-256. The Source Code Form of every file is available at the commit above.

## Optimizer and lineage requests stored in Git (8 files)

Four queries are each used once by the optimizer unit and once by the lineage
unit, giving eight JSON request files.

- Three queries come from the SQLMesh "sushi" example project,
  https://github.com/SQLMesh/sqlmesh, commit
  `f6527ffbd8d54d91e26fab7d36c8f4a061bc6451`:
  `examples/sushi/models/waiter_revenue_by_day.sql`,
  `examples/sushi/models/customer_revenue_by_day.sql` and
  `examples/sushi/models/customer_revenue_lifetime.sql`. Their table schemas are
  read from the literal column declarations in `examples/sushi/models/orders.py`,
  `order_items.py` and `items.py` at the same commit. Licence: Apache License 2.0
  (Apache-2.0); full text in `SQLMesh-LICENSE`. The project has no NOTICE file
  at that commit.
- One query and its schema come from the "SQL Optimizer" section of SQLGlot's
  `README.md`, https://github.com/tobymao/sqlglot, commit
  `5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76`. Licence: MIT, Copyright (c) 2026
  Toby Mao; full text in `LICENSE.sqlglot`.

Changes made (these files are modified versions of the sources above):

- The SQLMesh `MODEL (...)` header of each query was removed.
- The SQLMesh `@start_date` and `@end_date` parameters were replaced with
  `CAST('2024-01-01' AS DATE)` and `CAST('2024-01-07' AS DATE)`.
- The SQLMesh schemas were collected into one schema mapping, with one added
  three-column table declaration (`customer_revenue_lifetime`).
- The SQLGlot README query text and schema are unchanged.
- Each query was wrapped, together with its schema and a SQL dialect, in a JSON
  request object; lineage requests also carry a request identifier.

The recipe is `public/optimizer/prepare.py`; source URLs and SHA-256 values are in
`public/optimizer/provenance.json` and `public/alternates/provenance.json`.

## TPC-H/TPC-DS-derived requests (fetch-only, not distributed)

The remaining 341 public requests (121 optimizer, 121 lineage, 99 structural-diff)
are derived from SQLGlot's TPC-H and TPC-DS optimizer fixtures. They are not
stored in this repository or in the task image. The public preflight downloads
`tests/fixtures/optimizer/tpc-h/tpc-h.sql`,
`tests/fixtures/optimizer/tpc-ds/tpc-ds.sql` and `tests/helpers.py` from SQLGlot
commit `5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76`, and
`workload/fetch_inputs.py` derives the request bytes locally;
`environment/public-input-packet.json` commits their sizes and SHA-256 values.
These requests are not TPC benchmark results.

## TPC material in the SQLGlot source tree

The image build (`environment/prepare.py`) deletes SQLGlot's
`tests/fixtures/optimizer/tpc-h/`, `tests/fixtures/optimizer/tpc-ds/` and
`benchmarks/parse.py` (which embeds a TPC-H query) before the pinned tree is
committed, so no image layer contains them. Some other upstream test files
keep TPC-related text as part of SQLGlot's MIT-licensed source, and are left
unchanged because the upstream test gate runs them:

- `tests/dialects/test_duckdb.py` contains one TPC-H Query 1 select list (in
  DuckDB's prefix-alias syntax) as a parser test.
- `tests/test_anonymize.py` (`test_tpcds_query_reserialized`) contains one
  TPC-DS query as an anonymizer test input.
- `tests/helpers.py` and `tests/gen_fixtures.py` define the TPC-H and TPC-DS
  table schemas (table and column names and types) that SQLGlot's own tests use.

## Held-out inputs

Held-out inputs and expected outputs are not part of the agent image. Their
sources and changes are described in `tests/notices/HELDOUT.md` in the task
directory.
