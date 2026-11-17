# Suite input attributions

This file covers every public input of this task. Public inputs are derived at image build
from the sources below. Exact URLs, sizes and SHA-256 values are in `payload-public.json`.
The derivations are the builders in `workload/driver/derive.py`. Licence texts referenced
below are in `suite-inputs/` and `mirror/` next to this file.

Held-out inputs are not part of this image. Their sources, licences and attributions ship
with the verifier, in the task's `tests/notices/` directory.

## Ranked documentation search

The ranked-documents unit searches SQLite FTS5 databases built from project documentation.
The build recipe is `workload/corpus.json` and `tools/author_sqlite_ranked.py` in the
repository.

Public inputs (`public-ranked-input.db`, `public-ranked-requests.tsv`):

- CPython documentation, `Doc/` reStructuredText files
  (https://github.com/python/cpython, commit `8183fa5e3f78ca6ab862de7fb8b14f3d929421e0`),
  PSF-2.0. Licence and file-specific notices: `ranked-documents/cpython/` (upstream
  `LICENSE`, `Doc/license.rst` and the documentation files that carry their own notices).
- Requests documentation, `docs/` reStructuredText files
  (https://github.com/psf/requests, commit `b25c87d7cb8d6a18a37fa12442b5f883f9e41741`),
  Apache-2.0. Licence and notice: `ranked-documents/requests/LICENSE` and
  `ranked-documents/requests/NOTICE`.

Changes made: documentation files were split at their section headings into sections
(source path and line, heading, body); exact duplicate sections, auto-generated files and
excluded paths were dropped; the sections were loaded into a SQLite table with an FTS5
index. The request lists are built from section headings that end in a question mark,
rewritten as FTS5 queries over the heading's words. Section text is otherwise unchanged.

## Creative Commons Attribution-ShareAlike

- Stack Exchange Data Dump (2024-04): Seasoned Advice (Cooking Stack Exchange,
  https://cooking.stackexchange.com/). Contributions are CC BY-SA 2.5, 3.0 or 4.0 by
  contribution date; the derived database and request list are adaptations licensed under
  CC BY-SA 4.0. Attribution, licence links, changes made and the share-alike statement:
  `STACK-EXCHANGE.md`.

## Creative Commons Attribution

The CC BY 4.0 legal code is in `mirror/cc-by-4.0-legalcode.html`
(https://creativecommons.org/licenses/by/4.0/).

- Global Power Plant Database, World Resources Institute
  (https://datasets.wri.org/datasets/global-power-plant-database), CC BY 4.0, obtained as
  Datasette's `global-power-plants.db` (https://datasette.io/global-power-plants.db).
  Attribution: Global Energy Observatory, Google, KTH Royal Institute of Technology in
  Stockholm, Enipedia, World Resources Institute, Global Power Plant Database.
  Changes: plants split by whole country into disjoint public and held-out SQLite
  databases and request lists; table values, schema and index definitions unchanged.
- GeoNames country dumps CA, MX and US
  (https://www.geonames.org/, https://download.geonames.org/export/dump/), CC BY 4.0.
  Attribution: GeoNames, https://www.geonames.org/. Changes: loaded into SQLite
  gazetteer tables, split by state parity (R*Tree unit) and, for the integrity-check
  input, deliberately damaged on selected b-tree pages.
- Auckland Transport GTFS (https://gtfs.at.govt.nz/gtfs.zip), CC BY 4.0.
  Attribution: Auckland Transport. Changes: GTFS tables loaded into SQLite.
- Tatoeba per-language sentence export deu
  (https://tatoeba.org/en/downloads, https://downloads.tatoeba.org/exports/per_language/),
  CC BY 2.0 FR (https://creativecommons.org/licenses/by/2.0/fr/; legal code:
  `suite-inputs/cc-by-2.0-fr-legalcode.html`). Attribution: Tatoeba contributors,
  https://tatoeba.org/. Changes: bzip2 decompression only.
- Google Research Open Buildings v3 (mirror inputs), CC BY 4.0: see
  `mirror/ATTRIBUTION.txt`.

## MIT License

- Chinook sample database v1.4.5 (https://github.com/lerocha/chinook-database, release
  asset `Chinook_Sqlite.sqlite`, tag commit `4a944a942426e1f3263fe539155fb7ef92b04b4a`),
  integrity-check unit, used unmodified. Copyright (c) 2008-2024 Luis Rocha. Licence text:
  `suite-inputs/chinook-database-LICENSE.md` (upstream `LICENSE.md` at that commit).

## NYC Open Data

- NYC Citywide Payroll Data (Fiscal Year) (https://data.cityofnewyork.us/d/k397-673e),
  published on NYC Open Data (https://opendata.cityofnewyork.us/) by the
  Office of Payroll Administration. Subject to the terms of use linked from NYC Open
  Data (https://www.nyc.gov/main/terms-of-use) and published under the NYC Open Data Law,
  NYC Administrative Code Title 23, Chapter 5
  (https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-216807).
  Changes: selected pay components loaded into a SQLite ledger table.

## Public domain

- U.S. Government works: USGS ComCat earthquake catalog, NOAA NCEI Storm Events and
  GHCN-Daily, EPA AQS daily data.
- OurAirports data (https://ourairports.com/data/, mirror inputs), dedicated to the
  public domain under the Unlicense (https://unlicense.org/): see `mirror/ourairports-LICENSE`.
