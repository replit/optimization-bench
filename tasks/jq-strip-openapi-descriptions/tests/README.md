# Separate verifier

`test.sh` is a compatibility wrapper around the shared judge; score with
`judge.score`. `Dockerfile` uses the pinned task image. The agent
receives only `environment/`, never this directory or the shared judge.

`heldout.lock.json` authenticates the independently built held-out manifest
and every held-out input, expected body and license reference. The nine OpenAPI
specifications, the City of Toronto GeoJSON file, their ten expected outputs
and the nine upstream licence files are stored with Git LFS under `heldout/`.
The OpenAPI expected outputs were generated twice by pinned pristine jq in
fresh no-network containers and are byte-identical. The held-out GH Archive hour
is fetch-only: it is downloaded from its pinned URL and authenticated by size and
SHA-256 outside the repository, and only length and SHA-256 commitments of its
outputs are stored.

See the [judge guide](../../../docs/judge.md), the
[public runtime packet guide](../../../docs/public-runtime-packets.md)
and the task's `provenance.json` for source commits, licenses, group separation,
and reproducible commands. Availability is not scored qualification or a speedup claim.

`judge.json` defines resource and upstream gates. Protected upstream expectations
remain in this directory where required. Public replay and no-op/negative-control
logs are written to the operator-selected output directory, not committed here.
See `docs/judge.md` for admission, reconstruction and verifier-host requirements.

## Whole-task noise checks

`differential/run_aa.py` forwards the unchanged `tools/qualify_aa.py` options. For the held-out
scope it first uses the shared verifier's external fetch-only acquisition.
GH Archive compressed hours and decoded inputs never enter the source worktree.
Use a fresh `--output` outside Git.

The four units use the existing equal-weight pattern. `differential/validate_gharchive.py`
checks complete outputs for all units, the new units' 1.3-second sandbox floor,
and source-rebuilt wrong-result, omitted-work, missing-output and correct-but-slow
controls. New references are complete stdout/stderr/status lengths and SHA256
commitments, not redistributed event-derived output bodies.
