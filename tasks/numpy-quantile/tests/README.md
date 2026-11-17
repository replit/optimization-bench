# Separate verifier

Score with `judge.score`; `test.sh` is a thin entry point to the same verifier. `Dockerfile`
uses the pinned task image. The agent
receives only `environment/`, never this directory or the shared judge.

`heldout.lock.json` authenticates `heldout/manifest.json` and any declared held-out
inputs. Recovered/requalified held-out assets are available through Git LFS;
their lock and requalification provenance remain authoritative. They do not claim
that the original held-out bodies were recovered. Public LFS is staged separately.

`judge.json` defines resource and upstream gates. Protected upstream expectations
remain in this directory where required. Public replay and no-op/negative-control
logs are written to the operator-selected output directory, not committed here.
See `docs/judge.md` for admission, reconstruction and verifier-host requirements.

Suite units: each of the eight additional performance units (interp-equalize, busday-calendar,
random-dists, strings-text, genfromtxt-catalog, loadtxt-csv, datetime-iso, float-format) has one
held-out case in `heldout/cases.json` (`heldout-<unit>`), content-locked through
`heldout/manifest.json` and `heldout/files/`. Their 35 input bodies under `heldout/data/units/`
(1,397,302,827 bytes, about 1.4 GB) are stored in Git LFS and authenticated against
`heldout/inputs.lock.json` and `heldout.lock.json`. Without them (LFS not pulled) the task
reports its held-out payload as unavailable rather than passing.

Sources, licences and attributions for every held-out input are in `notices/HELDOUT-INPUTS.md`,
with the licence texts it cites.
