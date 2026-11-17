# Ruff task attribution and acquisition

The task acquires original source from public upstreams, before execution:

| Component | Pin | License retained in the image |
| --- | --- | --- |
| Ruff | `3265ed1f944c98bb4c04d632fbefb1257cdb583d` | MIT, `/app/ruff/LICENSE` |
| pandas | `1da7e563b2e46ef0665c4dca83cd9ca9890f90ee` | BSD-3-Clause, `/app/projects/pandas/LICENSE` |
| FastAPI | `f5c6e9b4f9cadc1bf0f9a1fd672e621206b63007` | MIT, `/app/projects/fastapi/LICENSE` |

Verbatim copies of these three license files are also retained under
`/app/notices/upstream/`. The complete upstream project trees retain their own
additional notices and licensed fixtures. Rust's distribution is pinned in
`environment/source.lock.json`; Cargo dependencies are fixed by the original
Cargo.lock and retain their licenses in `/assets/vendor`. The pinned public
Node/Debian base retains its original package copyright files.

The JSON task inputs contain the frozen CLI arguments and inventory commitments,
not redistributed project bodies. `prepare.py` anonymously fetches the exact
public commits and authenticates their complete original file/tree inventories.
No private mirror, registry credential, employee account or operator filesystem
is part of that acquisition.

No recovered, unauthenticated held-out project body is redistributed here.
