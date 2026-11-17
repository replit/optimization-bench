# Separate verifier

`test.sh` is a compatibility wrapper around the shared judge; score with
`judge.score`. `Dockerfile` uses the pinned task image. The agent
receives only `environment/`, never this directory or the shared judge.

`heldout.lock.json` authenticates `heldout/manifest.json` and every held-out
input, expected result and reference. The held-out set (Mozilla bigquery-etl SQL,
MPL-2.0) ships in this repository: its 429 inputs, 429 expected results and 122
references are stored with Git LFS under `heldout/`, outside the agent image,
and scoring fails closed if any body is missing or altered. Sources, licence and
changes made for the held-out inputs and expected outputs are in
`notices/HELDOUT.md`. The TPC-H/TPC-DS-derived public inputs are fetch-only: the
public preflight downloads SQLGlot's pinned `tpc-h.sql`, `tpc-ds.sql` and
`tests/helpers.py` and derives them locally (see
`../environment/notices/INPUTS.md` and `../environment/public-input-packet.json`).

`judge.json` defines resource and upstream gates. Protected upstream expectations
remain in this directory where required. Public replay and no-op/negative-control
logs are written to the operator-selected output directory, not committed here.
See `docs/judge.md` for admission, reconstruction and verifier-host requirements.
