# Protected correctness gates

The unchanged source pin is Git v2.56.0 at
`a018953688f1b10bddf91bff8747068f5f4746a4`. Only the original 15 xdiff
C/header files are editable. Tests, fixtures, snapshots, drivers, build scripts,
configuration and all other production files are protected.

- `upstream.py` runs the five original diff suites (t4000, t4002, t4033, t4048,
  t4050; 139 passing identities, ledger unchanged) plus t4015 (whitespace), t4055
  (context), t6403 (merge-file) and t6427 (diff3 conflict markers): 334 TAP identities.
  The hardened gate container fails two t6403 identities on the pristine build (scripted
  helpers cannot read the `/root` install prefix), so the gate requires the exact pristine
  identity/outcome ledger, not success. `t4015.129` (move detection with submodules) is
  skipped. Suites run eight at a time with the same per-suite command as `t/Makefile`.
  This is a selected subset, **not the whole Git suite**. Relocated test paths are
  regenerated without rebuilding source/objects; the selected candidate's own copied
  binaries are used, never a cached baseline.
- The shared public oracle compares complete status/stdout/stderr for seven
  distinct-input units, including every full patch and merged-file byte. No tolerant comparison,
  digest-only adapter, truncation or repeated-call multiplier is used.
- The shared C interface gate covers the public `xdiff.h` declarations,
  structs and callbacks. A verifier-only ABI-field control must be rejected.
- `differential/` uses a seeded generator,
  exact full-observation comparator, production Git CLI diff/merge calls,
  deterministic shrinking and two full-observation replays of a broken control.
  Three seeds cover six families and 23 fresh-process atoms each. Raw cases and
  control source are verifier artifacts, never participant source.
- Native process-state and static runtime-settings checks remain enabled.

`differential/validation/` records the new untimed public controls and fresh
correctness validation. Timing evidence for the former identical-call
shortstat workload does not qualify the new workload.

The independent held-out lock is **available**. Original histories remain
fetch-only: the verifier acquires and authenticates their pinned commit,
canonical object closure, strict-fsck receipt and native checkout outside the
repository. No repository body is retained in Git or LFS. Three complete
pristine observations are retained through Git LFS.
The small judge-only `tests/baseline-outputs.json` sidecar supplies their
length/hash references to the unchanged adapter and is authenticated directly
by `heldout.lock.json`; it is never part of an agent Docker context.

All four original reserved repositories are used: Neovim, mdBook, Click and
ESLint. The original reservation file is retained verbatim as historical
selection policy, not as a statement that construction is still pending.
The final set has 2,164 distinct non-merge commits per repository: 1,082 full
Myers patches, 541 Myers shortstats and 541 histogram patches each. That is
8,656 distinct commits, 26.416% of the public count, with no repeated-call
multiplier or input mutation.

Disjointness covers repository identity, original commit identity and the
path-agnostic `(pre-image OID, post-image OID)` pair being diffed. An initial
selection shared 11 incidental individual file bodies and some diff pairs
with public data. Every commit containing a public pair was excluded
deterministically in original topological order, including tiny/empty-file
changes. The final selection has no shared pair; its one shared incidental
individual body is disclosed in the audit. No file-size exemption is used.

`heldout/references/selection-freeze.json`, `overlap-preflight.json` and
`reference-generation.json` record exact exclusions, source pins, equality of
two fresh no-network pristine captures and one non-scoring runtime sizing
pass. All three final units exceeded one second without noise flags. This is
not an A/A session, optimization run or performance qualification. Held-out
sets are released (`docs/heldout-lfs.md`): repository identities and pins are
public, histories stay fetch-only, and the expected output bodies ship with
their source licences in `tests/notices/heldout-expected/`.

Four later xdiff units add 6 fetch-only held-out repositories (none shared with the public
split), the same complete-observation comparison and judge-only length/hash sidecar:
Express (MIT), JUnit 5 (EPL-2.0), mypy (MIT, with three PSF-2.0 files), pip (MIT),
Prometheus (Apache-2.0) and SQLAlchemy (MIT). Their pinned commits and licences
are listed in `notices/heldout-expected/NOTICE.md`; no output body derived from
them is stored.
