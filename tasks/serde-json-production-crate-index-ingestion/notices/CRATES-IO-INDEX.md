# crates.io index data

The `registry-ingest` unit reads crate version records from the crates.io
package index, maintained by the crates.io team of the Rust project:

- Repository: https://github.com/rust-lang/crates.io-index
- Pinned commit: `8b1b03f5370e310770ba69fe28b9e8e738765047`
- Index README at that commit:
  https://github.com/rust-lang/crates.io-index/blob/8b1b03f5370e310770ba69fe28b9e8e738765047/README.md

Distribution status: fetch-only. No crates.io index file is stored in this
repository, in Git LFS or in the task image. Public and held-out index files are
downloaded from
`https://raw.githubusercontent.com/rust-lang/crates.io-index/8b1b03f5370e310770ba69fe28b9e8e738765047/<path>`
and checked against the byte length and SHA-256 recorded in `payload.json` and
`tests/heldout.lock.json` before use. The six held-out expected outputs derived
from these records are regenerated locally and are not redistributed either.

Terms: use of crates.io and its data is governed by the crates.io policies
(https://crates.io/policies) and data access policy
(https://crates.io/data-access). The Git index is hosted on GitHub, so GitHub's
Acceptable Use Policies
(https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies)
also apply to downloads from it. The licences of individual crates and of the
crates.io policy text are not asserted to license the index metadata. Anyone who
downloads these inputs obtains them from the publisher under the publisher's
terms; this benchmark grants no rights in them.

The `geojson-typed` unit does not read crates.io data. Its public inputs (Natural
Earth, japan-topography) and held-out inputs (geoBoundaries gbOpen) are listed with
their licences and attribution in `GEOJSON-SOURCES.md`.
