# Separate verifier

`test.sh` is a compatibility verifier wrapper; score with `judge.score`. `Dockerfile`
uses the pinned task image. The agent
receives only `environment/`, never this directory or the shared judge.

`heldout.lock.json` authenticates `heldout/manifest.json` and any declared held-out
inputs. The held-out set is newly built (`provenance: built-new`) from
independently sourced inputs. It uses 31 complete, unchanged
source files from pinned CPython and Git releases, under Python-2.0 and GPL-2.0-only
terms, except Git's `compat/regex/regexec.c` (from the GNU C Library), which is
LGPL-2.1-or-later. Both fresh, no-network pristine captures produced byte-identical
complete outputs. Source paths, revisions and per-file licences are in the held-out
manifest; verbatim notices are retained in this task's `notices/cpython.txt`,
`notices/git.txt` (Git's COPYING, GPL-2.0) and `notices/git-LGPL-2.1.txt` (Git's
LGPL-2.1 file, from the same commit).
The frozen 31 inputs and their expected outputs are unchanged.

The second held-out unit decodes nine complete, hash-pinned release archives.
Their bodies are fetch-only: verification obtains them through credential-free
HTTPS into a temporary verifier view, never into the checkout, agent
context, Git, or LFS. The original integrity-error and concatenated-stream
fixtures remain correctness-only cases. All 42 complete expected results and
the 33 retained input bodies are authenticated by the held-out lock.
The repository retains, in Git LFS, the 31 source files and the 2 correctness
fixtures (`heldout/files/`, 33 bodies) and all 42 expected results
(`heldout/expected/`). Only the nine release archives are fetched. The retained
bodies must be included in a hydrated release export or recovered with a
task-scoped LFS pull before verification.

`judge.json` defines resource and upstream gates. Protected upstream expectations
remain in this directory where required. Public replay and no-op/negative-control
logs are written to the operator-selected output directory, not committed here.
See the [judge documentation](../../../docs/judge.md) for admission,
reconstruction and verifier-host requirements.
The additional native control protocol and its executable component smoke
test are documented in `fuzz/README.md`. An untimed four-CPU component pass is
not an eight-CPU scorer, CPU-matrix, or timing qualification.
