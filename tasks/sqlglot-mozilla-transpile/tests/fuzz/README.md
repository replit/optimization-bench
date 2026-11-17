# SQLGlot contract-fuzz adapter

Task-owned hooks and a one-arm subprocess worker for pinned pure-Python
SQLGlot 30.21.0. This is an initial adapter/control slice, **not full v1.1
qualification**. No production source, environment, timing, shared judge/tools,
private inputs or historical sweep receipts are changed.

The shared controller owns admission, verdicts, coverage thresholds and mutation
adequacy. This adapter exposes exactly the approved hook shapes and an exact
values/errors/effects comparator. Exception class **and full message** are
contractual; messages are never stripped or normalized. Diagnostics, including
report-only memory peaks, do not affect comparison.

## Scope and public corpus

`surface.json` initially maps fourteen documented entrypoints. It explicitly
marks its authority incomplete: the corrected transitive re-export inventory,
remaining builders/schema/optimizer/lineage/executor/diff APIs, custom subclasses,
and complete dialect/option combinations are unresolved. No broad “parse API”
label is used to claim those symbols are covered.

`freeze_inventory.py` selects only the explicit original **public/train** split:
eight documents, SHA-sorted, with 0 < bytes <= 16,000. It copies original SQL bytes
and hashes, never expected oracle outputs. Grammar, comments, truncation and
bounded size mutations supplement the public corpus. Seeds are 1729/2718/31415;
approved per-candidate budgets are frozen in `adapter.json`, not yet consumed.

AST operations receive SQL text and construct their trees using public parsing.
Manually serialized ASTs and private helper invocations are not admitted.
Malformed SQL passed to documented parse APIs remains a valid error-domain case.
The historical missing-`Join.this` private state is never executed by an arm.

## Worker and replay

`driver.py` accepts a protected rebuilt arm runtime, case path and authenticated
build hash, then writes the standard observation envelope. Only one library arm
is imported per process. Reporting uses trusted Python JSON, not the candidate.
It records complete results, exception type/message, stdout/stderr, ordered log
messages, process settings and thread/fd state around each explicit API call.
Interleaving and reinitialization create fresh public objects; no private reset
or malformed AST mutation is used.

Run task-owned unit tests:

```sh
python -m unittest discover -s tasks/sqlglot-mozilla-transpile/tests/fuzz -p 'test_*.py' -v
```

Run the three real canonical controls with an authenticated pristine runtime and
the integrator's actual shared controller:

```sh
python tasks/sqlglot-mozilla-transpile/tests/fuzz/validate_controls.py \
  --runtime /scratch/pristine-runtime \
  --shared-contract /scratch/integrator/judge/fuzzing_contract.py \
  --output /scratch/sqlglot-controls.json
```

The valid public semantic mutant and exact-error-message mutant are disposable
pristine package copies, compiled and replayed twice in fresh workers. They are
not optimized candidate patches. The private malformed-Join control proves zero
precondition/arm/oracle invocations through the actual shared controller.

## Instrumentation and remaining acceptance work

Framework2's pinned-task Python overlay provides Hypothesis/Atheris/coverage;
use `/opt/fuzz-python/bin/python`, including all child workers. This initial host
smoke/control evidence is not a substitute for overlay-based full campaigns.
`header-native-scope.json` binds an authenticated upstream archive scan, pinned
packaging/build evidence, and zero C/C++ headers/native source files. Optional
sqlglotc/sqlglotrs extras are not the protected pure-Python task feature.
Independent validation approved header-consumer N/A for this pinned pure-Python
feature only; it does not approve race/lifecycle or full-surface qualification.

Pending: corrected public-surface authority, full property/coverage-guided
campaigns, every candidate's changed line/branch/error-lifecycle target inventory,
1/2/8-CPU × five-repeat state/lifecycle replay, thread-creation/race applicability,
twenty compiled non-equivalent mutants and the required kill rate, comparable
per-unit memory reports, and final authenticated receipts. Missing evidence is
explicitly undertested, never a pass. No `tests/judge.json` changes are made before
integrator schema review. No PR is opened.

The task-owned engine entrypoints are `property_generate.py` (consumes
`--seed`/`--examples`, freezes actual ordered cases) and `coverage_guide.py`
(Atheris typed decoder, task import inside `instrument_imports`, one arm only).
The Atheris worker requires trusted `SQLGLOT_FUZZ_RUNTIME`,
`SQLGLOT_FUZZ_BUILD_SHA256`, and `SQLGLOT_FUZZ_CORPUS_OUT`. It retains actual
case order and observations for later isolated both-arm replay; discovery
alone is not a differential pass. Its cumulative worker memory is explicitly
not a per-unit measurement.

Initial pinned-task overlay feasibility failed because the production parent
retains `PIP_NO_INDEX=1` during the overlay's dependency install. The failed
Framework2 build receipt/log is retained under `receipts/overlay-attempt/`.
Framework2 fixed this with a command-local build override in its published
`297e2d3f53aad039deab9af51a133960737986ea`. The retry on the actual immutable
SQLGlot parent succeeded, with the parent interpreter and runtime offline
configuration preserved. The shared executor sanitizes image environment values;
trusted smoke steps explicitly carry `PIP_NO_INDEX=1`. Both attempts remain visible.

`instrumentation_smoke.py` replays actual SQLGlot code using the shared
DockerExecutor and approved `/opt/fuzz-python/bin/python`. Receipts under
`receipts/overlay-fixed/` bind the parent/runner image, source/build hashes,
Python 3.14.6, Hypothesis 6.170.12, Atheris 3.1.0 and coverage 7.17.0.
The smoke produces 30 property examples, 30 Atheris one-arm discovery observations,
and a real coverage.py branch export normalized across 48 SQLGlot files.
This is not a 45-minute differential campaign or changed-coverage adequacy.
The initial local development smoke with Hypothesis 6.151.9 is superseded as
instrumentation evidence, not relabeled as a qualified campaign.

Every mapped surface declares exact required error fields (`class` nonempty
string, `message` string), compatible with canonical RC validation before
comparison. The adapter also rejects missing error evidence independently.
The public `Expression.copy` probe mutates only a parser-produced copy through
public `Identifier.set`, then compares original/fresh trees. An additional
disposable identity-returning copy mutant proves that aliasing differences are detected
twice in fresh workers; no private or invalid AST state is used.
An earlier `copy.copy(self)` mutation was not killed and is not counted as a
non-equivalent mutant. The replacement explicitly returns the original object,
violating the documented deep-copy contract.

## Generic RC registrations

`rc_hooks.py` is loaded by `judge.fuzzing_generic.task_runners(task)`; no candidate
code registers or selects gates. The shared `RUNNERS` execute the CLI/header
requests. `rc_driver.py` runs all library arms inside the shared DockerExecutor,
then imports exactly one rebuilt runtime in each fresh/persistent child.

- CLI: pinned public `python -m sqlglot -` stdin transpilation, small SQL and
  6,000 public-grammar statements (84 KB), healthy/ENOSPC/EPIPE/closed-stdout
  requests. Inputs are watched; both arms and two replays are required.
- Library: four public semantic/copy/transform/error cases plus four caller
  callback-error cases. Every case has two fresh observations per arm; the
  persistent worker executes the same eight cases twice in one process.
- Callback faults: `Expression.transform` with `copy=True/False` and
  `Expression.walk(prune=...)`, raising ENOSPC/EPIPE after three public callback
  invocations. Full error class/message, callback visitation order, partial
  publicly mutated tree, and yielded prefixes are retained and compared.
- Actual execution affinity is sampled by `sched_getaffinity` inside **each**
  library observation. Per-call FD/descendant counts and concrete live
  TID/start-tick identities are retained, not inferred from thread counts.
- Applicability: independently approved no-header pure-Python feature.
  The pinned CLI declares stdout only, no output-file mode. The fourteen mapped
  APIs have no reader/writer short-transfer protocol; callable transform/prune
  arguments are **not** waived and are explicitly fault-tested.

`receipts/rc-mapped-signatures.json` retains exact signatures, pinned source
definitions, file hashes and source links. Full transitive/opts/custom-subclass
coverage is still incomplete. Generic registration and pristine/pristine smoke
are not candidate qualification, process-state acceptance, or performance proof.
The separate shared process-state gate remains mandatory.

The fresh **pristine-versus-pristine** physical CPU replay passed at 1/2/4/8 CPUs:
60 executed observations per arm per subset (480 total), with actual affinity
verified for every subset. See `receipts/cpu-subsets/summary.json`, the aggregate
receipt, complete compressed raw trees and per-file hash inventories.
This proves the registrations execute under the CPU checker, not that any
optimized candidate passed. Separate process-state acceptance remains required.

The first full replay was unmeasured because the local canonical shared generic
formatter lacked the CPU branch's `cpu_raw` retention. Its failed receipt and
complete raw evidence are preserved separately. The passing run used the exact
matching shared CPU/generic/policy files from `50ae082cea7badd2af6e10134bf1364d3857c8ef`,
loaded read-only; integration must include those companion shared changes.
