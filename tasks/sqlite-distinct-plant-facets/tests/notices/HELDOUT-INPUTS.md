# Held-out input sources and attributions

This file covers every held-out input of this task: the derived databases and request lists
under `tests/heldout/files/`, the reference outputs and state files under
`tests/heldout/references/`, and the provenance, notice and reference files under
`tests/heldout/data/`, all stored in Git LFS. The held-out data is not part of the agent
image. The public inputs are covered by `environment/notices/SUITE-INPUTS.md` and
`environment/notices/STACK-EXCHANGE.md`, which also ship in the image.

Exact upstream URLs, sizes and SHA-256 values of the raw files, and the derive recipe of every
derived input, are in `tests/heldout/manifest.json`; the stored bodies are pinned in
`payload.json` and `tests/heldout.lock.json`. The derivations are the builders in
`environment/workload/driver/derive.py`.

Licence identifiers are SPDX identifiers where one exists. Licence texts in this directory:

- `cc-by-4.0-legalcode.html`: Creative Commons Attribution 4.0 International legal code
  (https://creativecommons.org/licenses/by/4.0/legalcode)
- `cc-by-2.0-fr-legalcode.html`: Creative Commons Paternité 2.0 France legal code
  (https://creativecommons.org/licenses/by/2.0/fr/legalcode)
- `cc-by-sa-2.5-legalcode.html`, `cc-by-sa-3.0-legalcode.txt`, `cc-by-sa-4.0-legalcode.txt`:
  Creative Commons Attribution-ShareAlike 2.5, 3.0 and 4.0 legal code
- `stackexchange-data-dump-license.txt`: the Stack Exchange Data Dump's own `license.txt`,
  verbatim
- `northwind-SQLite3-LICENSE`: upstream `LICENSE` of jpwhite3/northwind-SQLite3 at commit
  `807e2172c45402a1873cfd68b51e36959470fd2e`
- `microsoft-sql-server-samples-LICENSE.txt`: upstream `license.txt` of
  microsoft/sql-server-samples at commit `8fe3fd0c9a825fef3ae6d601e97537b612d7b214`

The licence and file-specific notices of the held-out ranked documentation are stored with the
held-out data under `tests/heldout/data/ranked-notices/` (see below).

## Labels in `payload.json`

`payload.json` is hash-protected and left unchanged. Where its labels differ from this file,
this file is the precise record:

- The two held-out ranked-documents input rows (`heldout-ranked-input.db`,
  `heldout-ranked-requests.tsv`) are labelled "BSD and MIT". Their precise licence expression is
  `BSD-3-Clause AND BSD-2-Clause AND MIT`, as set out below.
- Held-out rows whose labels point at `environment/notices/SUITE-INPUTS.md`,
  `environment/notices/STACK-EXCHANGE.md` or `environment/notices/suite-inputs/northwind-SQLite3-LICENSE`
  are attributed in this file instead; the Northwind licence text is
  `tests/notices/northwind-SQLite3-LICENSE`.

## Distinct plant facets

- Global Power Plant Database, World Resources Institute
  (https://datasets.wri.org/datasets/global-power-plant-database), `CC-BY-4.0`, obtained as
  Datasette's `global-power-plants.db` (https://datasette.io/global-power-plants.db).
  Inputs: `heldout-db`, `heldout-tsv`.
  Attribution: Global Energy Observatory, Google, KTH Royal Institute of Technology in
  Stockholm, Enipedia, World Resources Institute, Global Power Plant Database.
  Changes: plants split by whole country into disjoint public and held-out SQLite
  databases and request lists; table values, schema and index definitions unchanged.

## Ranked documentation search

Inputs `heldout-ranked-input.db` and `heldout-ranked-requests.tsv`, and the reference output
`ranked-documents.bin` computed from them. Licence and file-specific notices are under
`tests/heldout/data/ranked-notices/`, one directory per project (named in parentheses below).

- Django documentation, `docs/` text files (https://github.com/django/django, release 5.2.7,
  commit `3cff3209e35a560f94801d428cf7f2a3ecb2a051`), `BSD-3-Clause` (`framework/`).
- Sphinx documentation, `doc/` reStructuredText files (https://github.com/sphinx-doc/sphinx,
  commit `847ad0c991e21db9daa02fec09acbd456f353300`), `BSD-2-Clause` (`documentation-tool/`).
- SQLAlchemy documentation, `doc/build/` reStructuredText files
  (https://github.com/sqlalchemy/sqlalchemy, commit `29e4255f413b9791e117b40ab12c76c9df6313d8`),
  `MIT` (`database-tool/`).
- PyPy documentation, `pypy/doc/` reStructuredText files (https://github.com/pypy/pypy,
  commit `413c9b7f57f5d40da72669857e97a636150fc533`), `MIT` (`interpreter-docs/`).

Copyright notices: each project's upstream licence file at the pinned commit, retained verbatim
in the directory named above, together with the file-specific notices of the documentation.
The held-out set's licence expression is therefore `BSD-3-Clause AND BSD-2-Clause AND MIT`.

Changes made: documentation files were split at their section headings into sections
(source path and line, heading, body); exact duplicate sections, auto-generated files and
excluded paths were dropped; the sections were loaded into a SQLite table with an FTS5
index. The request list is built from section headings that end in a question mark,
rewritten as FTS5 queries over the heading's words. Section text is otherwise unchanged.

## Suite units

| Source | Unit and inputs | Licence | Attribution | Changes |
| --- | --- | --- | --- | --- |
| GeoNames country dumps IN and ZA (https://download.geonames.org/export/dump/IN.zip, https://download.geonames.org/export/dump/ZA.zip; https://www.geonames.org/) | integrity-check: `integrity-check-geonames-IN-ZA.corrupt-s2.db` | `CC-BY-4.0` (`cc-by-4.0-legalcode.html`) | "GeoNames, https://www.geonames.org/" | Modified: loaded into an index-rich SQLite gazetteer database, then deliberately damaged on selected b-tree pages (seeded). |
| Northwind-SQLite3 `dist/northwind.db` (https://github.com/jpwhite3/northwind-SQLite3, commit `807e2172c45402a1873cfd68b51e36959470fd2e`) | integrity-check: `northwind.db` | `MIT` (`northwind-SQLite3-LICENSE`) | "Copyright (c) 2016 JP White". The upstream README describes it as a SQLite version of the Microsoft Access 2000 Northwind sample database. Microsoft publishes the Northwind sample database itself under the MIT License (https://github.com/microsoft/sql-server-samples, commit `8fe3fd0c9a825fef3ae6d601e97537b612d7b214`, `samples/databases/northwind-pubs/`): "Copyright (c) Microsoft Corporation" (`microsoft-sql-server-samples-LICENSE.txt`). | None: used unmodified, byte-identical to the pinned upstream file. |
| Tatoeba per-language sentence exports jpn and spa (https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences.tsv.bz2, https://downloads.tatoeba.org/exports/per_language/spa/spa_sentences.tsv.bz2; https://tatoeba.org/en/downloads) | tatoeba-create-index: `tatoeba-create-index-jpn_sentences.tsv`, `tatoeba-create-index-spa_sentences.tsv` | `CC-BY-2.0-FR` (https://creativecommons.org/licenses/by/2.0/fr/; `cc-by-2.0-fr-legalcode.html`) | "Tatoeba contributors, https://tatoeba.org/" | Modified: bzip2 decompression only. |
| USGS ComCat earthquake catalog, FDSN event GeoJSON for the first half of each month 2022-01 to 2022-06 (https://earthquake.usgs.gov/fdsnws/event/1/) | usgs-geojson-json: `usgs-geojson-json-quakes.db`, request list | U.S. Government work, public domain (17 U.S.C. 105) | None required; credited here to the U.S. Geological Survey. | Modified: events of the held-out half of networks (by a fixed hash of the network code) stored as compact GeoJSON documents in a SQLite table. |
| NOAA NCEI Storm Events Database, details files 2000-2009 (https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/) | storms-printf-report: `storms-printf-report-storms.db`, request list | U.S. Government work, public domain (17 U.S.C. 105) | None required; credited here to NOAA National Centers for Environmental Information. | Modified: events of the held-out half of states (by a fixed hash of the state) loaded into a SQLite table with selected columns; numeric columns typed. |
| GeoNames country dump US (https://download.geonames.org/export/dump/US.zip) | geonames-rtree: `geonames-rtree-places.db`, request list | `CC-BY-4.0` (`cc-by-4.0-legalcode.html`) | "GeoNames, https://www.geonames.org/" | Modified: places of the held-out half of first-level administrative divisions (by a fixed hash) loaded into a SQLite table with selected columns and an R*Tree index. |
| City of Los Angeles, City Employee Payroll (Current) (https://controllerdata.lacity.org/d/g9h8-fvhu) | decimal-ledger: `decimal-ledger-ledger.db`, request list | `CC-BY-4.0` (`cc-by-4.0-legalcode.html`) | "Los Angeles City Controller, https://lacontroller.org/" | Modified: the regular, overtime, other and benefit pay components per department and pay year loaded into a SQLite ledger table; non-numeric amounts dropped. |
| USGS National Water Information System (NWIS), daily discharge values for NY, PA, CA and TX, 2015-2024 (https://waterservices.usgs.gov/nwis/dv/) | percentile: `percentile-obs.db`, request list | U.S. Government work, public domain (17 U.S.C. 105) | None required; credited here to the U.S. Geological Survey. | Modified: daily mean discharge values per site and day loaded into a SQLite table; non-numeric values dropped. |
| Photography Stack Exchange contributors (https://photo.stackexchange.com/), Stack Exchange Data Dump 2024-04 | fkey-cascade: `fkey-cascade-se.db`, `fkey-cascade-requests-heldout` | `CC-BY-SA-4.0` adaptation of `CC-BY-SA-2.5`, `CC-BY-SA-3.0` and `CC-BY-SA-4.0` content | See "Photography Stack Exchange" below. | See below. |
| Translink GTFS, South East Queensland feed (https://gtfsrt.api.translink.com.au/GTFS/SEQ_GTFS.zip; dataset https://www.data.qld.gov.au/dataset/general-transit-feed-specification-gtfs-translink) | gtfs-departure-join: `gtfs-departure-join-gtfs.db`, request list | `CC-BY-4.0` (`cc-by-4.0-legalcode.html`) | "Translink, Department of Transport and Main Roads, Queensland" | Modified: GTFS agency, routes, trips, stops, stop times, calendar and calendar dates tables loaded into SQLite with selected columns. |
| NOAA NCEI Global Historical Climatology Network - Daily (GHCN-Daily), ten USC stations (https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/) | ghcn-window-series: `ghcn-window-series-ghcn.db`, request list | U.S. Government work, public domain (17 U.S.C. 105) | None required; credited here to NOAA National Centers for Environmental Information. | Modified: station metadata and daily precipitation, snow and temperature values loaded into SQLite tables. |
| GeoNames gazetteer records (https://www.geonames.org/, https://download.geonames.org/export/dump/) | batch-upsert and mirror-vacuum: `heldout-mirror-incoming`, `heldout-mirror-upsert-seed`, `heldout-mirror-before-vacuum` and the two state references `heldout-batch-upsert.state.jsonl.gz`, `heldout-mirror-vacuum.state.jsonl.gz` | `CC-BY-4.0` (`cc-by-4.0-legalcode.html`) | "GeoNames, https://www.geonames.org/" | Modified: records stored with the GeoNames dump header in the mirror cache and state formats. The acquisition record and source notice are `tests/heldout/data/mirror-acquisition.json` and `tests/heldout/data/mirror-notice-1.txt`. |

The request lists of each unit select identifiers or parameters from that unit's database
and carry the same licence and attribution.

## Photography Stack Exchange

The held-out foreign-key-cascade input uses a database and request list adapted from the Stack
Exchange Data Dump (2024-04 release, Internet Archive item https://archive.org/details/stackexchange),
published by Stack Exchange, Inc.

Content by the **Photography Stack Exchange contributors**, https://photo.stackexchange.com/
Archive: https://archive.org/download/stackexchange/photo.stackexchange.com.7z
(114,262,856 bytes, SHA-256 `64bbe87fa079e55067cf2a42c8cc6953332eebfa76db5f5703af3adc655e9f2b`).
Held-out database `fkey-cascade-se.db` (504,909,824 bytes, SHA-256
`301ae4b1b212ca51c9d03adaa76900c0ac508617fe6132632078047ff9ceb9c2`) and its request list
(607,716 bytes, SHA-256 `2a34b30fd37326025d0f5b30ffcb1c88d52de9417ba1b2edc34b6b5b54b751a1`),
stored in Git LFS under `tests/heldout/files/`.

Every post, comment and revision keeps its original identifiers and author fields, so it can be
attributed to its source: question `<id>` is `https://photo.stackexchange.com/q/<id>`, answer
`<id>` is `https://photo.stackexchange.com/a/<id>`, user `<id>` is
`https://photo.stackexchange.com/users/<id>`, and author names are in `users.display_name`,
`posts.owner_display_name`, `comments.user_display_name` and `post_history.user_display_name`.
The data dump's own attribution statement is retained verbatim in
`stackexchange-data-dump-license.txt`.

Licences. Stack Exchange user contributions are licensed by contribution date
(https://photo.stackexchange.com/help/licensing,
https://stackoverflow.com/legal/terms-of-service/public):

- contributed before 2011-04-08 (UTC): CC BY-SA 2.5,
  https://creativecommons.org/licenses/by-sa/2.5/ (legal code: `cc-by-sa-2.5-legalcode.html`);
- contributed from 2011-04-08 up to but not including 2018-05-02 (UTC): CC BY-SA 3.0,
  https://creativecommons.org/licenses/by-sa/3.0/ (legal code: `cc-by-sa-3.0-legalcode.txt`);
- contributed on or after 2018-05-02 (UTC): CC BY-SA 4.0,
  https://creativecommons.org/licenses/by-sa/4.0/ (legal code: `cc-by-sa-4.0-legalcode.txt`).

The licence of each post, comment and post revision is kept unchanged in the `content_license`
column of the `posts`, `comments` and `post_history` tables.

The derived database and request list are adaptations of this content and are licensed under
**CC BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/). CC BY-SA 2.5 (section 4(b))
and CC BY-SA 3.0 (section 4(b)) allow adaptations to be licensed under a later version of the
licence with the same licence elements.

Changes made:

- The XML files of the site archive were loaded into SQLite tables (`users`, `posts`,
  `comments`, `votes`, `post_history`, `post_links`, `badges`, `tags`) with declared foreign
  keys and indexes. Only the columns listed in `SE_TABLES` of
  `environment/workload/driver/derive.py` were loaded; integer columns were stored as integers.
- Referential cleanup: answers whose question is missing; comments, votes, post history and
  post links that refer to missing posts; and badges of missing users were deleted. Other
  references to missing posts or users were set to NULL.
- `ANALYZE` and `VACUUM` were run.
- The request list selects user and post identifiers from this database.
- No post, comment or revision text was edited.

Share-alike. If you share this database, request list, or material adapted from it (including
verifier outputs that contain its content), you must license it under CC BY-SA 4.0 (or a
licence that section 3(b)(1) of CC BY-SA 4.0 allows), keep this attribution, link the licence,
and state your changes. The content is provided as-is, without warranties (see section 5 of the
licence). Neither Stack Exchange, Inc. nor the contributors endorse this benchmark.
