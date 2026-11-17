# Held-out project licenses

Two held-out project trees under `tests/heldout/projects/` keep only the SQL
and configuration files that the verifier lints. Their upstream license files
are kept here instead of inside those trees: the runner authenticates each
tree's exact file inventory, so a file added there would change the scored
input.

The files below are byte-for-byte copies of each project's root license file
at the pinned commit recorded in `provenance.json` (`heldout.sources`). They
were extracted from the pinned source archives, whose SHA-256 values match the
`archive_sha256` pins.

| Held-out tree | Upstream project and commit | License file | SPDX |
| --- | --- | --- | --- |
| `tests/heldout/projects/mimic-bigquery/` | [MIT-LCP/mimic-code@303d26c](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/LICENSE) | [`heldout-projects/mimic-bigquery/LICENSE`](heldout-projects/mimic-bigquery/LICENSE) | MIT |
| `tests/heldout/projects/stripe-postgres/` | [stripe/pg-schema-diff@9ada471](https://github.com/stripe/pg-schema-diff/blob/9ada4710c28718312557c84cca1be4b2557eed54/LICENSE.md) | [`heldout-projects/stripe-postgres/LICENSE.md`](heldout-projects/stripe-postgres/LICENSE.md) | MIT |

The SQL and `.sqlfluff` files in those trees are unmodified copies of the
upstream files at the same commits (their SHA-256 values match the pinned
archives). The other held-out trees keep their upstream license files
in place: `pagila-schema/LICENSE.txt` (PostgreSQL),
`syntax-tree-evaluation/LICENSE` (Apache-2.0) and
`autofix-evaluation/origin-{1,2}/LICENSE` (PostgreSQL and Apache-2.0). The
Snowflake sf-samples license (Apache-2.0) is restored from
`environment/workload/driver/alternate-assets/` before execution.

## Expected outputs derived from held-out sources

`tests/heldout/expected/` holds the complete pinned SQLFluff 4.4.0 results for
the held-out units: lint reports for the configured projects and the Snowflake
samples, auto-fixed SQL for the pgmq (PostgreSQL) and pg_graphql (Apache-2.0)
files, and parse trees for the bigquery-utils (Apache-2.0) views. The auto-fixed
SQL and parse trees are modified versions of those source files: they were
changed mechanically by running unmodified SQLFluff with the task's
configuration. Each remains under its source file's own license, listed in
[RESEARCH.md](../../RESEARCH.md). The unmodified originals are available at the
pinned commits.
