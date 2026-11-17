# Independent contract controls

Run the initial suite from repository root:

```sh
python3 -m unittest judge.tests.test_fuzzing_contract_controls judge.tests.test_writer_faults -v
```

The synthetic header controls need host `cc` and `c++`; missing compilers are
an explicit failure to run controls, not skipped qualification. No network,
held-out inputs, optimized candidate source or task-source changes are used.
The first replay used GCC/G++ 15.2.0 on the independent Ubuntu scratch VM.
Those compiler calibrations are not a pinned-task-image campaign.

`controls.json` is an expectation/specification catalog. Its top-level
`qualification_status` stays `undertested`; `integration_status` distinguishes
real existing-comparator tests from pending task/shared-controller wiring.
Catalog-shape tests do not count as real SQLGlot preflight acceptance evidence.
Follow-up integration tests require the shared controller and Framework 2
modules merged into the repository (integrator `0f5e857b...` or later).

The historical SQLGlot recipe was supplied by the task owner without a patch
or optimizer trace and independently replayed from the hash-verified public
source archive in `tasks/sqlglot-mozilla-transpile/environment/source.lock.json`.
It creates a Join without `.this`, then directly calls the private
`Parser._implicit_unnests_to_explicit` via BigQuery. Its differing private
results must never reach a candidate oracle; a valid public-AST semantic change
and a changed public ParseError type/message must still be detected.
The recipe's observed private AttributeError is calibration, not a promised
exception message or an admitted campaign input.

To reproduce the independent SQLFluff pristine evidence using an environment
with the pinned SQLFluff source and hash-locked task dependencies installed:

```sh
python judge/tests/fixtures/fuzzing-contract-controls/replay_sqlfluff_ownership.py
```

The script checks the package version and both source-module SHA-256s before
running the legal mutable BlockConfig sequence. It reports the frozen
MatchResult's mechanical nested-dict mutability as **scope-unresolved**, not
an admitted mutation contract. It emits complete observations, runtime/source
and fixture identities, downloads nothing, and does not execute candidates.
The first independent replay used host Python 3.14.4, not the task's pinned
3.14.6 image; that runtime difference is explicit evidence scope.
Two fresh Python processes produced byte-identical evidence committed as
`sqlfluff-pristine-ownership.json`, SHA-256
`9ca8d5770a5908a15bc9f3ff820f36b24c0f0a50b19784a6f8aa835bdea309f3`.
The fixture/source hashes are checked by the suite. This is pristine ownership
evidence, not a golden candidate answer or a task-qualification receipt.

Current executable controls:

- Real serde writer semantic comparison ignores diagnostics/chunk partitions,
  but rejects changed accepted bytes, error/I/O category, panic and transport.
- The protected NumPy comparator accepts equivalent storage order, its 8eps
  tolerance, equal NaNs and signed zero; it rejects shape/dtype, finite/NaN and
  out-of-tolerance changes.
- Synthetic two-TU headers separately calibrate compile and link outcomes,
  valid language cells, GNU89 inline semantics, C++ C linkage and legal umbrella
  include preconditions; see `headers/README.md`.
- Real shared controller spies prove rejection precedes arm/oracle callbacks;
  public semantic/error-message differences remain detectable. Actual task
  admission hooks and fresh-process campaigns remain separate requirements.
- The actual Framework 2 matrix and coverage helpers are tested for cell
  identity, missing workers/branches and compile-vs-link attribution.
- `ownership_probe.py` retains live results across legal mutation/interleaving,
  recording semantic snapshots separately from root/child identity relations.
  Mutable shared caches and shallow wrappers differ from independent results;
  immutable interning and documented borrowed views are not forced to copy.
- Live thread/fd fixtures pass joined transient activity and detect resources
  alive at the API-return boundary, followed by verifier-side cleanup. They
  calibrate observations/policy, not a complete task lifecycle/race campaign.
- `cli-output.c` exercises real ENOSPC/EPIPE/closed-stdout failures on buffered
  flush and write paths. The fixture alone ignores SIGPIPE and promises exit 2;
  those choices are not imposed on actual tasks. A side-effect mutant has the
  same error/exit but changes an input file, which remains detectable.
- Approved Go panic frames are diagnostic while the headline/class, exit and
  contractual effects remain semantic. This is a representation control, not
  proof that a task's stderr parser is already wired.

Pending: SQLGlot owner hooks, supported task-header/ownership inventories, real
lifecycle/CPU/sanitizer/coverage campaigns and per-unit memory acquisition.
The canonical memory report-only decision policy is tested without declaring
any task's evidence obligations complete. No local path in this
scratch VM is claimed accessible from the integrator's environment.
