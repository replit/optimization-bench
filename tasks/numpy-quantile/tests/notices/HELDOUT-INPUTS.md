# Held-out input sources and attributions

This file covers every held-out input of this task: the arrays under `tests/heldout/data/arrays/`
and the unit inputs under `tests/heldout/data/units/`, both stored in Git LFS, and the expected
results under `tests/heldout/expected/`, which are computed from them. The held-out data is not
part of the agent image. The public inputs are covered by `environment/notices/suite-inputs.md`,
which also ships in the image.

Each prepared input is derived from pinned upstream bytes, recorded with URL, size and SHA-256 in
`tests/heldout/workload/corpus.json` (`parents`, warm batch) and in the task's `provenance.json`
(`suite_units`, `heldout`). The "Changes" column lists every transformation. No value was
edited, imputed or corrected. Attribution statements are reproduced in the form the publishers
request.

Licence identifiers are SPDX identifiers where one exists. `LicenseRef-` identifiers name the
publisher terms linked in the same row. Licence texts in this directory:

- `CC-BY-4.0.txt`: Creative Commons Attribution 4.0 International, from https://creativecommons.org/licenses/by/4.0/legalcode.txt
- `OGL-UK-3.0.txt`: Open Government Licence v3.0, from https://raw.githubusercontent.com/spdx/license-list-data/v3.29.0/text/OGL-UK-3.0.txt
- `CC-BY-3.0-NZ-legalcode.html`: Creative Commons Attribution 3.0 New Zealand, the legal code page https://creativecommons.org/licenses/by/3.0/nz/legalcode as published
- `CPython-3.12.7-LICENSE.txt` and `CPython-3.12.7-Doc-license.rst`: `LICENSE` and `Doc/license.rst` from the pinned `Python-3.12.7.tgz` (SHA-256 `73ac8fe780227bf371add8373c3079f42a0dc62deff8d612cd15a618082ab623`)

## Quantile warm batch

| Source | Inputs | Licence | Attribution | Changes |
| --- | --- | --- | --- | --- |
| NYC Taxi & Limousine Commission (TLC), Yellow Taxi Trip Records, months 2024-01, -03, -05, -07, -09, -11 (https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | `arrays/taxi-2024-*.npy` | `LicenseRef-NYC-Open-Data-Terms-of-Use`: NYC Open Data Terms of Use (https://www.nyc.gov/main/terms-of-use) | "Source: NYC Taxi & Limousine Commission (TLC) Trip Record Data." TLC states that the trip data was not created by the TLC and that TLC makes no representations as to its accuracy. | Modified: the four columns `trip_distance`, `fare_amount`, `tip_amount` and `total_amount` of 1,000,000 evenly spaced eligible rows per month are stored as float64 arrays. Rows outside the nominal month or with non-finite values are excluded. Values are unchanged. |
| UCI Machine Learning Repository, ElectricityLoadDiagrams20112014 (https://archive.ics.uci.edu/dataset/321/electricityloaddiagrams20112014) | `arrays/electricity-2014-block1.npy`, `arrays/electricity-2014-block2.npy` | `CC-BY-4.0` | Trindade, A. (2015). ElectricityLoadDiagrams20112014 [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C58C86 | Modified: the 2014 rows of a fixed customer pool are split into two float64 arrays. Values are unchanged; zeros are kept. |
| NOAA Physical Sciences Laboratory (PSL), NCEP-NCAR Reanalysis 1, daily mean air temperature at sigma level 0.995 (`air.sig995`), years 2018, 2019, 2022 and 2023 (https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html) | `arrays/weather-*.npy` | `LicenseRef-US-Government-Work`: U.S. Government data, no use restrictions | "NCEP-NCAR Reanalysis 1 data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov" | Modified: the netCDF variable `air` is decoded to float32 and stored as a 365x73x144 array per year. The full grid is kept; values are unchanged. |

## Suite units

| Source | Unit | Licence | Attribution | Changes |
| --- | --- | --- | --- | --- |
| AWS Terrain Tiles, skadi format (Tilezen joerd), tiles S13W072, S14W072, S16W069, S33W070 (https://registry.opendata.aws/terrain-tiles/) | interp-equalize | `LicenseRef-Terrain-Tiles-Sources`: tiles are composites of the open elevation sources in joerd's attribution notice; SRTM is public domain (U.S. Government) | The joerd required attribution, reproduced verbatim in `environment/notices/suite-inputs.md` (pinned to https://github.com/tilezen/joerd/blob/0b86765156d0612d837548c2cf70376c43b3405c/docs/attribution.md). | Modified: `.hgt.gz` decompressed; big-endian int16 3601x3601 grids converted to native-endian int16 `.npy`. Values are unchanged. |
| City of Chicago Data Portal: Building Permits (https://data.cityofchicago.org/d/ydr8-5enu) and Business Licenses (https://data.cityofchicago.org/d/r5kz-chrr) | busday-calendar | `LicenseRef-Chicago-Data-Portal-Terms-of-Use` (https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html) | The City of Chicago disclaimer, reproduced verbatim below. | Modified: only the date columns are used; rows with all dates in 1990-2039 are kept; dates are converted to `datetime64[D]`. |
| DataSF, City and County of San Francisco: Building Permits (https://data.sfgov.org/d/i98e-djp9) | busday-calendar | `PDDL-1.0` (dataset metadata; https://opendatacommons.org/licenses/pddl/1-0/) | None required. "Source: DataSF, Building Permits." | As above. |
| City of Austin, Texas: Issued Construction Permits (https://data.austintexas.gov/d/3syk-w9eu) | busday-calendar | `LicenseRef-Public-Domain`: dataset metadata licence "Public Domain U.S. Government" (https://www.usa.gov/government-works) | "City of Austin, Texas - data.austintexas.gov" (dataset attribution field). | As above. |
| City of Chicago Data Portal: Crimes - 2001 to Present, Chicago Police Department (https://data.cityofchicago.org/d/ijzp-q8t2), 2010-2019 | datetime-iso | `LicenseRef-Chicago-Data-Portal-Terms-of-Use` (https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html) | The City of Chicago disclaimer, reproduced verbatim below. The Chicago Police Department adds that the records may rest on preliminary, unverified reports and that it does not guarantee their accuracy, completeness, timeliness or sequencing (dataset description). | Modified: only the `date` and `updated_on` timestamps are used, as fixed-width text; each five-year export is split into thirds; empty fields are kept. |
| The World Bank, World Development Indicators: SP.POP.TOTL, SP.DYN.CBRT.IN, SP.DYN.CDRT.IN, SM.POP.NETM (https://data.worldbank.org/) | random-dists | `CC-BY-4.0`, with the World Bank's additional terms (https://data.worldbank.org/summary-terms-of-use) | "The World Bank: World Development Indicators: United Nations Population Division, World Population Prospects; national statistical offices; Eurostat, Demographic Statistics; United Nations Statistics Division, Population and Vital Statistics Report." The World Bank terms ask redistributors to pass on this acknowledgement requirement, which the terms URL in this row does. | Modified: groups are (country or aggregate, year) rows that have all four series; births and deaths are derived as round(population x rate / 1000). |
| Python Software Foundation, CPython 3.12.7 source release (https://www.python.org/ftp/python/3.12.7/Python-3.12.7.tgz) | strings-text | `PSF-2.0` (CPython `LICENSE`; code examples in the documentation are also `0BSD`; incorporated third-party components are listed in `Doc/license.rst`). Texts in `CPython-3.12.7-LICENSE.txt` and `CPython-3.12.7-Doc-license.rst`. | "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation; All Rights Reserved" | Summary of changes, as PSF-2.0 section 3 requires: the lines of `Lib/**/*.py` (including `Lib/test`) and `Doc/**/*.rst` are extracted; trailing `\r` removed; lines longer than 80 code points hard-wrapped; at most 300,000 lines per group; stored as four fixed-width Unicode arrays. Line text is otherwise unchanged. |
| GeoNet (Earth Sciences New Zealand), New Zealand earthquake catalogue, quakesearch CSV 2005-2025 (https://quakesearch.geonet.org.nz/; dataset DOI https://doi.org/10.21420/0S8P-TZ38) | genfromtxt-catalog | `LicenseRef-CC-BY-3.0-NZ`: Creative Commons Attribution 3.0 New Zealand (text in `CC-BY-3.0-NZ-legalcode.html`) | "We acknowledge the New Zealand GeoNet programme and its sponsors NHC, Earth Sciences NZ, LINZ, NEMA and MBIE for providing data/images used in this study." GeoNet content is copyright Earth Sciences New Zealand. | Modified: 21 yearly CSV exports are merged into four multi-year files; columns are renamed to ObsPy names; the trailing `Z` is removed from times. Values are unchanged. |
| UCI Machine Learning Repository, Gas sensor array under dynamic gas mixtures, `ethylene_CO.txt` (https://archive.ics.uci.edu/dataset/322/gas+sensor+array+under+dynamic+gas+mixtures) | loadtxt-csv | `CC-BY-4.0` | Fonollosa, J. (2015). Gas sensor array under dynamic gas mixtures [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5WP4C | Modified: the first 1,440,000 data rows as four 360,000-row slices; whitespace separators replaced by commas. Tokens are unchanged. |
| UK Environment Agency, real-time flood-monitoring API, river level readings for 174 stations (https://environment.data.gov.uk/flood-monitoring/doc/reference) | float-format | `OGL-UK-3.0` (text in `OGL-UK-3.0.txt`) | "this uses Environment Agency flood and river level data from the real-time data API (Beta)" | Modified: the last 10,000 level readings per station; the `value` column parsed to float64; stations in four groups. |
| UCI Machine Learning Repository, SGEMM GPU kernel performance (https://archive.ics.uci.edu/dataset/440/sgemm+gpu+kernel+performance) | float-format | `CC-BY-4.0` | Paredes, E. & Ballester-Ripoll, R. (2017). SGEMM GPU kernel performance [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5MK70 | Modified: the Run1-Run4 runtime columns parsed to float64. |
| UCI Machine Learning Repository, Online News Popularity (https://archive.ics.uci.edu/dataset/332/online+news+popularity) | float-format | `CC-BY-4.0` | Fernandes, K., Vinagre, P., Cortez, P., & Sernadela, P. (2015). Online News Popularity [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5NS3V | Modified: the first 20,000 rows; all numeric columns after `url` and `timedelta` parsed to float64. |

### City of Chicago disclaimer

The City of Chicago Data Terms of Use require applications and derivative uses of portal data to
show this disclaimer:

"This site provides applications using data that has been modified for use from its original
source, www.cityofchicago.org, the official website of the City of Chicago. The City of Chicago
makes no claims as to the content, accuracy, timeliness, or completeness of any of the data
provided at this site. The data provided at this site is subject to change at any time. It is
understood that the data provided at this site is being used at one’s own risk."
