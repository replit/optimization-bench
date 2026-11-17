# Protected xdiff coverage

Three frozen seeds each generate one transport batch containing six API
families and 23 atomic calls (69 calls total per build across the seeds).
Inputs are bounded mutations and recombinations of a public pinned source
excerpt, never held-out ancestry windows or precomputed reference results.

- Four documented diff algorithms with full patch output.
- Whitespace, blank-line, word-diff and numstat options.
- Empty, larger, insertion/deletion and no-final-newline text.
- Binary/NUL and long/odd-size content.
- Clean, unilateral and conflicting three-way merge-file calls.
- Swaps, repeats and exact-equal calls with fresh process isolation.

Every atomic observation uses a fresh process and directory. Complete exit
status, stdout and stderr enter an exact observation comparator; expected
nonzero diff/conflict statuses remain values, not discarded errors. No clocks,
source patch, model solution or held-out expected output enters the harness.
Default DockerExecutor isolation observes process leftovers too. Shrinking is
deterministic and preserves a reproduced complete-observation mismatch.

The trusted Python wrapper imports no library code or mutable Git instance.
Grouping does not reuse a Git process, configuration or directory: the process
boundary is retained for every atomic observation. Family tags remain in every
complete result, and the exact comparator drops nothing. This avoids repeatedly
cloning a 3.25 GB image under VFS merely to launch the same isolated CLI calls.

Each atom also receives fresh HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME and TMPDIR,
empty per-atom global/system Git configs and no inherited GIT object/index/
worktree/alternates/config paths. The wrapper imports no candidate code.
Family and stable atom IDs enter each observation; the framework retains seed.
A deliberate shared-/tmp state control must be detected on the affected atom
before grouping can be qualified. No such leak-control pass is implied here.

This is an API/CLI differential oracle, not a timing workload or a full Git
conformance-suite claim. It must actually pass unchanged-original seeds and
reject/replay a behavior-changing control before any qualification is claimed.
