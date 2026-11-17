# Third-party data policy

Adopted by the AI team at Replit on 2026-11-15 for the public OptimizationBench release.
It follows common practice for public benchmarks (for example MLPerf, MLE-bench,
SWE-bench-style datasets and BigCode's The Stack). It is not legal advice and
grants no rights in third-party material.

## Rules

1. **Host only what may be shared.** The repository and public images contain
   only third-party bodies whose license permits redistribution, with their
   notices.
2. **Everything else ships as a recipe.** Other inputs are pinned URLs with
   SHA-256 (and, where needed, a deterministic rebuild script). Users download
   them from the original
   publisher and must accept that publisher's terms themselves.
3. **Original licenses travel with the data.** Every source keeps its license and
   attribution notice. Using any part of this benchmark means complying with
   those licenses and terms.
4. **Copyleft content ships intact.** See the obligations below.
5. **No non-commercial or unreleasable inputs.** Inputs labeled non-commercial,
   or that we cannot release, are removed or the Task is held.
6. **Takedown.** Rights holders can ask for removal; see below.

## Copyleft obligations

| License | Where | How the release satisfies it |
|---|---|---|
| GPL-2.0 / GPL-3.0 / LGPL | git (Git source; Git and HAProxy input histories), xz (GNU source archives; six public libgit2 files, GPL-2.0-only WITH GCC-exception-2.0 linking exception; 14 held-out Git files, GPL-2.0-only, and their derived expected outputs; held-out Git `compat/regex/regexec.c`, LGPL-2.1-or-later) | Shipped complete and unmodified with their license files (`COPYING`, `LGPL-2.1`, `haproxy-LICENSE`, per-archive `COPYING`, `git-LGPL-2.1.txt`). HAProxy: GPL-2.0 core, LGPL-2.1 headers, OpenSSL exemption. Only the Task's own Git build is compiled, from full source in the image. |
| GPL-2.0-or-later, PostgreSQL, MIT (file-specific) | sqlfmt (`tests/fuzz/native/native-public-reference.bin`, 25,993,649 bytes, LFS) | Formatter output derived from 844 public PostgreSQL/PostGIS files: modified (reformatted) copies under each source's license, with the GPL-2.0 text, PostGIS `LICENSE.TXT`, PostgreSQL `COPYRIGHT` and the address_standardizer MIT notice in `environment/notices/`, a derivation notice in `tests/fuzz/native/notices/NATIVE-REFERENCE-OUTPUTS.md`, and the unmodified sources at the pinned URLs in `payload.json`. PostGIS `spatial_ref_sys.sql` holds EPSG dataset records, acknowledged with the IOGP/EPSG terms of use (https://epsg.org/terms-of-use.html). |
| AGPL-3.0-only, GPL-2.0-or-later | goldmark (3 + 1 Markdown documents) | Shipped unmodified as input text with `NOTICE.md`, the AGPL-3.0 text, pandoc's `COPYRIGHT` and `COPYING.md` and the GPL-2.0 text. They are data for a parser, not compiled or run as a network service. |
| MPL-2.0 | sqlglot (Mozilla bigquery-etl SQL and held-out expected outputs), sqlfluff (the public bigquery-etl file, fetched unmodified), sqlfmt held-out native reference, git held-out expected outputs (mdBook text) | Unmodified files ship with the MPL-2.0 text. Modified files (formatted, auto-fixed or other derived outputs) are marked as modified in their Task notices, ship with the MPL-2.0 text and point to the pinned source commit, where the unmodified source is available. |
| CC-BY-SA-4.0 (adaptation) | sqlite (Stack Exchange data: Seasoned Advice public, Photography held-out) | Attribution to Seasoned Advice and Photography Stack Exchange contributors, dated per-post CC BY-SA 2.5/3.0/4.0 terms, CC BY-SA 4.0 adaptation and share-alike statement, and legal code in the Task's `environment/notices/STACK-EXCHANGE.md` and `suite-inputs/` (Seasoned Advice) and `tests/notices/HELDOUT-INPUTS.md` (Photography). |
| CC-BY-SA-3.0 | sqlfmt (reformatted output of one PostGIS documentation file inside the native reference) | Attribution, license link, modification statement and share-alike statement in `tests/fuzz/native/notices/NATIVE-REFERENCE-OUTPUTS.md`. |
| Per-file (ODbL, CC BY, CC BY-SA, public domain, others) | serde_json (194 held-out geoBoundaries gbOpen files) | Fetch-only, never hosted; each file's licence and the geoBoundaries citation are listed in the Task's `notices/GEOJSON-SOURCES.md`. |

SQLFluff's held-out syntax-tree outputs come from GoogleCloudPlatform/bigquery-utils (Apache-2.0), not from bigquery-etl.

## Source terms recorded outside frozen Tasks

Additional source terms for the following Tasks are recorded here rather than
inside the Task directories, so that their image contexts remain unchanged.

### numpy-quantile: input sources

Every public input source is attributed in the Task's `environment/notices/suite-inputs.md`
(shipped in the image) and every held-out source in `tests/notices/HELDOUT-INPUTS.md`, each
with its licence, the attribution the publisher asks for and the changes made. Licences
include U.S. Government works, NYC Open Data and City of Chicago terms, CC BY 4.0,
CC BY 3.0 NZ (GeoNet), OGL-UK-3.0 (UK Environment Agency), PDDL-1.0 (San Francisco) and
the PSF licence (CPython text); the licence texts ship with the notices.

The `warm-batch` unit's arrays are prepared from NYC Taxi & Limousine Commission
yellow-taxi trip records (2023 public months, 2024 held-out months), published at
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page and through NYC Open
Data. Terms: NYC Terms of Use (https://www.nyc.gov/main/terms-of-use). New York City
Administrative Code Title 23, Chapter 5 requires public data sets to be made available
without restrictions on their use. TLC makes no representation as to the accuracy of
the data. Any additional terms set by TLC apply.

The public `strings-text` batch built from Project Gutenberg texts was removed on
2026-11-17, because Project Gutenberg's clearance is U.S.-only and 16 of its 175 texts
could not be confirmed public domain in the UK/EU.

### sqlite-distinct-plant-facets: held-out mirror inputs

The five held-out mirror files (`heldout-mirror-incoming`, `heldout-mirror-upsert-seed`,
`heldout-mirror-before-vacuum` and their two state references) hold GeoNames gazetteer
records: their stored source header is the GeoNames dump schema (`geonameid`, `name`,
`asciiname`, ...). GeoNames data is licensed under Creative Commons Attribution 4.0
(https://www.geonames.org/). Attribution: "GeoNames, https://www.geonames.org/".
The public mirror inputs come from OurAirports (public domain) and Google Open Buildings v3
(CC BY 4.0), with notices in the Task's `environment/notices/mirror/`.
The GeoNames attribution for these files ships in the Task's `tests/notices/HELDOUT-INPUTS.md`.

### sqlglot-mozilla-transpile: alternate inputs

`public/alternates/provenance.json` pins every public alternate to SQLGlot (MIT) test
fixtures and SQLMesh (Apache-2.0) example models. Held-out alternates are revisions of
Mozilla's public bigquery-etl SQL (MPL-2.0). "Private origin" means held-out, not
non-public data.

The TPC-DS and TPC-H query text originates from the TPC specifications, whose terms do
not allow general redistribution. The benchmark's TPC-based inputs are not stored in this
repository or in the task image: `environment/workload/fetch_inputs.py` downloads SQLGlot's pinned `tpc-h.sql`,
`tpc-ds.sql` and `tests/helpers.py` from SQLGlot's public repository, checks their
SHA-256 pins, and derives the TPC-based public inputs locally. The image build removes
SQLGlot's `tests/fixtures/optimizer/tpc-h` and `tpc-ds` directories, and the two upstream
tests that read them are declared exclusions in `release.json`. It also removes SQLGlot's
`benchmarks/parse.py`. The short TPC examples that remain inside SQLGlot's own MIT-licensed
upstream test sources are listed in the Task's `environment/notices/INPUTS.md`.

## Takedown requests

If you hold rights in material included in or referenced by this benchmark and
want it removed, open an issue in this repository or contact the AI team at
Replit. We remove confirmed material from the next release and list the change in
the release notes.
