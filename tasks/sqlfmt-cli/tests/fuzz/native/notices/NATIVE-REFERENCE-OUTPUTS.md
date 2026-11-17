# Native reference outputs: derivation and terms

This notice covers the two frozen native reference files that ship through Git
LFS in this repository. Neither file is copied into the task image.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `tests/fuzz/native/native-public-reference.bin` | 25,993,649 | `adf203ec208f7295eedc0c1c28ba55e3a1a54c84b9a47f90419784b2056869a1` |
| `tests/heldout/references/native-batch.bin` | 17,086,707 | `f5b9f3ada6efc2441628aef94c11dea42fa1548433205f0f97a7c1b89c46742c` |

## How they were made

Each file is the complete, ordered output of one run of the pinned sqlfmt
(upstream commit `8c676b51ea62e6ce33221e0d9d6d8a43256c942c`) calling
`format.Format` on every document in a native batch. For each document it holds
either the formatted SQL text or the error sqlfmt returned. Each formatted text
is a modified version of its original source file: it is the source as
reformatted by sqlfmt, with nothing else added. The changes were made
mechanically by the formatter no later than 2026-11-16, when the files were
first published with OptimizationBench v1.0. Reformatting may move or alter
source comments, including copyright headers, so the notices listed below and
the unmodified originals at the pinned commits are authoritative.

## Public reference: PostgreSQL and PostGIS sources

`native-public-reference.bin` is derived from the 844 public documents listed in
`native-public-origins.json` (original path, commit and SHA-256 for each).
Download URLs and pins for the unmodified originals are in the task's
`payload.json` (`native-source-*` rows).

| Source | Commit | Documents | Terms | Notice in this repository |
| --- | --- | ---: | --- | --- |
| postgres/postgres | `3d6a828938a5fa0444275d3d2f67b64ec3199eb7` | 194 | PostgreSQL License | `environment/notices/PostgreSQL-native-COPYRIGHT.txt` |
| postgres/postgres | `3c17926eedd51c4094db7c62f59950918044ab1c` | 48 | PostgreSQL License | `environment/notices/postgres-3c17926eedd5-COPYRIGHT` |
| postgis/postgis | `9e97dbf69ea7f8507acc2e11b326770e31dc8a82` | 526 | GPL-2.0-or-later | `environment/notices/postgis-native-COPYING` (GPL-2.0 text), `environment/notices/postgis-native-LICENSE.TXT` |
| postgis/postgis | `30e191ee94f2a99964283066adfe268d71cdf15a` | 76 | GPL-2.0-or-later, except as noted below | `environment/notices/postgis-30e191ee94f2-COPYING` (GPL-2.0 text), `environment/notices/postgis-30e191ee94f2-LICENSE.TXT` |

Exceptions within the PostGIS `30e191ee94f2` documents:

- The 12 documents under `extensions/address_standardizer/` are under that
  directory's MIT license (Copyright 2006-2013 Stephen Woodbridge; Copyright
  (c) 2008 Walter Bruce Sinclair). Its verbatim text is
  `environment/notices/postgis-30e191ee94f2-address_standardizer-COPYING`.
- One document, `doc/html/image_src/st_azimuthmath.sql`, is in the PostGIS
  documentation tree. PostGIS's `LICENSE.TXT` states that PostGIS
  documentation is under the Creative Commons Attribution-ShareAlike 3.0
  license (https://creativecommons.org/licenses/by-sa/3.0/). Its formatted
  output in the native reference is an adaptation of that file (attribution:
  the PostGIS Project, https://postgis.net/; change made: reformatted by sqlfmt)
  and is licensed under CC BY-SA 3.0 (share-alike).

EPSG Geodetic Parameter Dataset: one document from the PostGIS `9e97dbf69ea7`
commit, `spatial_ref_sys.sql`, consists of coordinate reference system records
from the EPSG Geodetic Parameter Dataset, and its sqlfmt result is part of the
native public reference. Ownership of the EPSG Dataset by the International
Association of Oil & Gas Producers (IOGP) is acknowledged. The data is used,
copied and distributed here, free of charge, subject to the EPSG Terms of Use
(https://epsg.org/terms-of-use.html); as those terms require, recipients are
informed of them here. IOGP provides the data "as is", without warranty. sqlfmt
changes only the layout of the SQL (white space, line breaks and keyword case);
it does not change the string or numeric literals that hold the EPSG records.

The PostGIS OGC conformance test suite (`extras/ogc_test_suite/`) is excluded
from the native batches at every commit.

The formatted PostGIS results are modified versions of GPL-2.0-or-later
programs and are distributed under the same license. The complete GPL-2.0 text
is in the `COPYING` notices listed above. The formatted output is itself in
source form, and the unmodified sources are available at the pinned commits.
The formatted PostgreSQL results remain under the PostgreSQL License, whose
copyright notice and permission text are retained above.

## Held-out reference: Mozilla bigquery-etl sources

`tests/heldout/references/native-batch.bin` is derived in the same way from the
2,470 held-out documents listed in `tests/heldout/native-origins.json`. They
come from mozilla/bigquery-etl at the four commits pinned in
`native-corpus.json` and are under the Mozilla Public License 2.0. The
formatted results are modified Covered Software under MPL-2.0. The full
license text is in this directory (`bigquery-etl-*-LICENSE`), and the
unmodified source files are available at the pinned commits.

The held-out CLI expected outputs in `tests/heldout/expected/` are sqlfmt output
for Chinook (MIT) and PostgreSQL (PostgreSQL License) inputs, modified in the
same way. Their license notices are in `tests/fuzz/notices/`.
