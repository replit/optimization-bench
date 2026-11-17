# SQLFluff workload provenance and distribution

The four public units and their four held-out counterparts retain the original
source pins, SQL bytes, configurations, expected outputs and comparators.
Their public reproduction commands are in [tests/README.md](tests/README.md).
No historical timing receipt qualifies this restored task composition.

## Source and input provenance

SQLFluff 4.4.0 is built from commit
`0348222a3bcdb0226563f7349c5a39beb0641955`, archive SHA-256
`e0afbbc9db5ceda286c660caffa640a6aea92ba99e964b9b7522207e01322a38`.
Its MIT license remains in the fetched source tree and installed wheel.
All downloads happen before workload execution.

| Unit | Split | Source project | Pinned commit | License (SPDX) |
| --- | --- | --- | --- | --- |
| Configured-project lint | public | [pgr0ss/pgledger](https://github.com/pgr0ss/pgledger/tree/d591b7693a89ddcdb527c2e59f4f702983235f69) | `d591b76` | MIT AND BSD-3-Clause (`vendor/scoville-pgsql-ulid/` is BSD-3-Clause) |
| Configured-project lint | public | [HTTPArchive/almanac.httparchive.org](https://github.com/HTTPArchive/almanac.httparchive.org/tree/46992bf44af8617c64dad69fe706a500bbf206f9) | `46992bf` | Apache-2.0 |
| Configured-project lint | public | [dbt-labs/jaffle-shop-classic](https://github.com/dbt-labs/jaffle-shop-classic/tree/fd7bfacae4f497ff044a6a0275268676bf1b64c3) | `fd7bfac` | Apache-2.0 |
| Configured-project lint | held-out | [stripe/pg-schema-diff](https://github.com/stripe/pg-schema-diff/tree/9ada4710c28718312557c84cca1be4b2557eed54) | `9ada471` | MIT |
| Configured-project lint | held-out | [MIT-LCP/mimic-code](https://github.com/MIT-LCP/mimic-code/tree/303d26c623dcc9c49cc0f204468d4acc2f063797) | `303d26c` | MIT |
| Configured-project lint | held-out | [devrimgunduz/pagila](https://github.com/devrimgunduz/pagila/tree/ce6e114236bb828e5ec0dc835f65f1bf1b9179f5) | `ce6e114` | PostgreSQL |
| PostgreSQL auto-fix | public | [maplibre/martin](https://github.com/maplibre/martin/tree/726b46e40b2617a5d6104f3ce14d84d12170b909) | `726b46e` | MIT OR Apache-2.0 |
| PostgreSQL auto-fix | public | [PostgREST/postgrest](https://github.com/PostgREST/postgrest/tree/29a95f823bd1c70ab92653d3c3e5cf86835832f0) | `29a95f8` | MIT |
| PostgreSQL auto-fix | held-out | [pgmq/pgmq](https://github.com/pgmq/pgmq/tree/8efb078dbe7afe16c83be72e9fffefbb03ebd9e9) | `8efb078` | PostgreSQL |
| PostgreSQL auto-fix | held-out | [supabase/pg_graphql](https://github.com/supabase/pg_graphql/tree/3327437620a1a620f411d7f9290b020eefee1508) | `3327437` | Apache-2.0 |
| Snowflake/Jinja lint | public | [Snowflake-Labs/sfquickstarts](https://github.com/Snowflake-Labs/sfquickstarts/tree/f7fcfaba78cbea3aeaf34bc8470c2ad051c3edaa) | `f7fcfab` | Apache-2.0 |
| Snowflake/Jinja lint | held-out | [Snowflake-Labs/sf-samples](https://github.com/Snowflake-Labs/sf-samples/tree/a291149cf20f8b52fc2cd31f92b09671c415bee2) | `a291149` | Apache-2.0 |
| BigQuery syntax export | public | [mozilla/bigquery-etl](https://github.com/mozilla/bigquery-etl/tree/c5dcc248916a4eb19b1b37cb4780fa794bb1fa56) | `c5dcc24` | MPL-2.0 |
| BigQuery syntax export | held-out | [GoogleCloudPlatform/bigquery-utils](https://github.com/GoogleCloudPlatform/bigquery-utils/tree/d5bde4fb0d2dbfa0477f1f2a6d87edda65bb7907) | `d5bde4f` | Apache-2.0 |

Each license was read from the project's own root license file at the pinned
commit (for pgledger, also the vendored directory's own `LICENSE`). The same
SPDX values are recorded in `environment/public-workloads.lock.json` (public
projects), `provenance.json` `heldout.sources` (configured-project held-out
projects), `tests/heldout/autofix-sources.json` and the held-out alternate
license files. Mozilla bigquery-etl is MPL-2.0, a file-level copyleft license;
every other source above is under a permissive license.

`environment/public-workloads.lock.json` records complete public project
inventories, original paths and fixed revisions. It preserves 126 distinct
Almanac SQL files rather than claiming a full-repository stress test. Original
configuration, ignores, Jinja builtins and the five Jaffle Shop models remain.
The auto-fix and alternate units process each declared original SQL target once.
No corpus is repeated, padded or replaced with generated SQL.

Verifier provenance is retained in `provenance.json`'s `heldout.sources`,
`tests/heldout/inputs.lock.json`, `tests/heldout/autofix-sources.json` and
`tests/heldout/alternate-sources.json`. The MIMIC inputs are public SQL program
source, not patient records or access-controlled clinical datasets. Benchmark
configuration and case descriptors are supplied in the release packet.
`tests/heldout.lock.json` and `payload.json` authenticate all retained verifier
bodies and the complete original expected-output serializations.

## Notices

Source projects retain their own licenses; SQLFluff's MIT license does not
relicense the input projects. Preserve the complete release packet, not only
the SQL files. The public build obtains original project notices and includes
the auto-fix notices under `/app/notices/autofix/`. Mozilla's original MPL-2.0
notice and license remain with its publicly obtainable, unmodified SQL source.
HTTP Archive's code and written documentation have different terms; retain its
original LICENSE, README and file-level attribution.

The verifier project trees retain PostgreSQL (Pagila and pgmq), Apache-2.0
(pg_graphql, sf-samples and bigquery-utils), and their original per-file notices.
The Snowflake shared Apache license is already bound in the protected
`environment/workload/driver/alternate-assets/` metadata and restored by the
unchanged client before execution. The MIMIC and pg-schema-diff trees keep
only their linted files, so their upstream MIT license files are kept as
byte-for-byte copies in [tests/notices/](tests/notices/README.md) and are also
reproduced below. The held-out expected outputs are SQLFluff results (lint
reports, auto-fixed SQL and parse trees) derived from these sources; see that
notice for the modification statement. This document stays outside the agent
Docker context.

### MIT-LCP/mimic-code

Pinned source:
https://github.com/MIT-LCP/mimic-code/tree/303d26c623dcc9c49cc0f204468d4acc2f063797

```text
MIT License

Copyright (c) 2019 MIT Laboratory for Computational Physiology

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### stripe/pg-schema-diff

Pinned source:
https://github.com/stripe/pg-schema-diff/tree/9ada4710c28718312557c84cca1be4b2557eed54

```text
The MIT License

Copyright (c) 2023- Stripe, Inc. (https://stripe.com)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
