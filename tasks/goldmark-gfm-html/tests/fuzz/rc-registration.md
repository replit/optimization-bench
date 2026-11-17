# Goldmark generic-gate registration

`judge.fuzzing_generic.task_runners` loads `rc_hooks.py` as a task-local module.
The generic verdict comes from actual callback execution and shared comparison,
not from this registration or its unit tests.

| Gate | Native path and scope |
| --- | --- |
| `stream_faults` | Go renderer calls with healthy, short, and failing public writers; full bytes and required errors are compared. |
| `repeat_in_process` | Fresh and persistent public calls, A/B/A ordering, and caller-visible lifecycle facts. |
| `aliasing` | Independent public AST construction, getter results, legal mutation, interleaving, and fresh instances. |
| `header_consumers` | Not applicable: the pinned public API is Go, with no C/C++ header surface. |
| `cli_faults` | Conditionally not applicable: the executable is the protected benchmark driver, not the library's public API; both writer-fault and repeat callbacks must exist **and actually pass**. |

`CLI_CASES` requests the conditional exclusion and names both covering callbacks.
It never supplies a passing verdict. Removing either callback removes the
applicability evidence; a missing or failed gate remains pending or failed.
The frozen workload's exact complete-output comparator is unchanged.

The stream probe is a bounded option/fault matrix, not an exhaustive Goldmark
API proof. AST aliasing covers declared independent-document construction and
metadata mutation; it does not generalize to undocumented sharing or concurrent
use. Differential generation supplies additional parser/renderer variation,
but does not replace upstream tests or the protected corpus.

Native receipts bind selected build/source and immutable image identities,
compiler execution, requested and observed affinity, and complete observations.
The shared CPU gate replays real probes on 1/2/4/8 physical logical-CPU subsets;
metadata presence and archived eight-CPU fixtures cannot satisfy that gate.
The ordinary image's runtime and Go configuration are not changed for these
probes. No performance result is implied.
