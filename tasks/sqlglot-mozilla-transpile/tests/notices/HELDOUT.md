# Held-out inputs and expected outputs: sources, licence and changes

Every held-out input of `sqlglot-mozilla-transpile` comes from Mozilla
bigquery-etl, https://github.com/mozilla/bigquery-etl, licensed under the Mozilla
Public License 2.0 (MPL-2.0). The full licence text is in
`../environment/notices/Mozilla-MPL-2.0`. The held-out bodies are stored with Git
LFS under `heldout/` and authenticated by `heldout.lock.json`; they are not part of
the agent image.

## Inputs

| Unit | Count | Source commit(s) | Form |
|---|---:|---|---|
| Conversion | 327 | `1819e636d900f935b9b464b19b00d05aa207d210` | 122 `query.sql` and 205 `view.sql` files, unmodified |
| Optimizer | 36 | `c5dcc248916a4eb19b1b37cb4780fa794bb1fa56` | JSON request: query SQL unmodified, plus table schemas |
| Column lineage | 48 | `c5dcc248916a4eb19b1b37cb4780fa794bb1fa56` | JSON request: query SQL unmodified, plus table schemas |
| Structural diff | 18 | `6acdebb4856b6786d9c1b54d79eaef9b3b15d243`, `c5dcc248916a4eb19b1b37cb4780fa794bb1fa56`, `335f1fb4396a3af85aa755f82c588084b287d302` | JSON request: the same file at two of these commits, SQL unmodified |

Changes made to the optimizer, lineage and diff inputs: each query is wrapped in a
JSON request object. For optimizer and lineage requests, the table schemas
published in the repository are added, with BigQuery `RECORD`/`REPEATED` field
types written as `STRUCT`/`ARRAY` types, and tables without an explicit project or
dataset are resolved to the query's own directory. The SQL text itself is not
edited. Original repository paths and SHA-256 values are recorded in
`heldout/original-manifest.json`, `heldout/conversion-expansion-provenance.json`,
`heldout/optimizer-provenance.json` and `heldout/alternates-provenance.json`.

## Expected outputs

The held-out expected outputs are produced by running the pinned, unmodified
SQLGlot baseline on the inputs above: DuckDB transpilations of the conversion
files, optimized SQL with its typed expression tree, column-lineage graphs, and
structural edit scripts between file revisions. Because they reproduce and
transform the Mozilla SQL, they are modified versions of MPL-2.0 Covered Software
and are distributed under MPL-2.0. The unmodified source of every file is
available at the bigquery-etl commits listed above, at the repository path
recorded in the provenance files.
