# Third-party input notices

The benchmark's license does not replace the licenses of its input snapshots.
Retain these notices when distributing source inputs or expected outputs.

- Public SQLAlchemy/Django project terms and public API/range terms are in
  `../../../environment/notices/`; the locked project archives retain their licenses.
- The held-out Faker tree retains `LICENSE.txt` (MIT), and the Google Cloud
  Retail tree retains `LICENSE` (Apache-2.0).
- `heldout/` retains the exact upstream root license files for the held-out
  documentation fragments and NumPy range source. `heldout/sources.json`
  records their pinned commits, anonymous HTTPS acquisition URLs, sizes and
  SHA-256 hashes. Repository/path attribution remains in every input body.

## Licenses of the six `NOASSERTION` sources

The frozen `tests/heldout/sources.json` records `NOASSERTION` for six source
repositories and says that "a reviewer licence sign-off is still pending". That
file is a hash-pinned held-out input commitment and is left byte-for-byte
unchanged, so its wording is historical. This notice and
`heldout/sources.json` beside it supersede it: each source's root license file
at the pinned commit was read and identified, and the SPDX id is now recorded
as `licence_spdx` in `heldout/sources.json`.

| Source | Pinned commit | License file (retained here) | SPDX |
| --- | --- | --- | --- |
| yt-project/yt | `d48eb7eb5287` | `yt-project_yt/COPYING` | BSD-3-Clause |
| getlogbook/logbook | `ce2772f50eee` | `getlogbook_logbook/LICENSE` | BSD-3-Clause |
| django-commons/django-hosts | `e538c46a9f07` | `django-commons_django-hosts/LICENSE` | BSD-3-Clause |
| ics-py/ics-py | `3b7d07295f60` | `ics-py_ics-py/LICENSE.rst` | Apache-2.0 |
| ServiceNow/BrowserGym | `27a90ce81509` | `ServiceNow_BrowserGym/LICENSE` | Apache-2.0 |
| numpy/numpy | `7be480da5f24` | `numpy_numpy/LICENSE.txt` | BSD-3-Clause |

Evidence: each license file above is the exact upstream file, fetched from the
pinned raw URL and authenticated by size and SHA-256 in `heldout/sources.json`
(checked by `tests/fuzz/test_public_release.py`). yt's `COPYING` states that
yt is under the Modified (3-clause) BSD License; the logbook, django-hosts and
NumPy files are the 3-clause BSD text; ics-py's `LICENSE.rst` is the full
Apache License 2.0; BrowserGym's `LICENSE` is the standard Apache-2.0 notice
(Copyright 2024 ServiceNow). The full Apache-2.0 text is also retained in
`heldout/mongodb_mongo-python-driver/LICENSE`.

The other held-out fragment sources retain their upstream MIT, BSD-2-Clause,
BSD-3-Clause or Apache-2.0 texts, with the SPDX id recorded in
`heldout/sources.json`. No input bytes, source attribution, fixture, golden
output or frozen held-out manifest was changed to add these notices.

## Modified files in the held-out expected outputs

The held-out expected outputs in `tests/heldout/expected/` contain third-party
source code after Black reformatted it, so they are modified versions of the
held-out input files. The only change made is reformatting by Black:

- `snippets-heldout.out`: documentation code fragments passed through
  `black.format_str` with each project's blacken-docs settings. Fragments come
  from the 18 warm-snippet repositories in `heldout/sources.json`. Five of them
  are Apache-2.0: mongodb/mongo-python-driver, nnaisense/evotorch,
  intel/neural-compressor, ServiceNow/BrowserGym and ics-py/ics-py. As section
  4(b) of the Apache License 2.0 requires, this notice states that the code
  from those projects in this file was modified (reformatted by Black). Their
  licence files are retained in `heldout/<owner>_<repository>/`.
- `ranges-heldout.out`: numpy/numpy files (BSD-3-Clause) after
  `black.format_file_contents` on the recorded line ranges.
- `faker-reformat.out`: the joke2k/faker tree (MIT) after
  `black --line-length 79`; its `LICENSE.txt` is retained in the tree.
