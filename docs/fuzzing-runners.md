# Verifier-only language runners and header-consumer matrix

The `tools/fuzz_runners/` package and its tests provide
instrumentation/build/capability/coverage **evidence**, not candidate verdicts,
coverage thresholds or performance scores. Canonical admission and policy live
in `judge/fuzzing_contract.py` and the shared fuzz controller. No production
`environment/` files, timing hosts, sweep candidates or historical receipts are
changed by these runners.

## Build against the actual task image

Use an immutable task image, including its toolchain and cached dependencies:

```sh
python -m tools.fuzz_runners.build_image \
  --language python --task-image 'repository@sha256:ACTUAL_TASK_IMAGE_DIGEST' \
  --tag local-task-fuzz --out /tmp/task-fuzz-image
python -m tools.fuzz_runners.smoke \
  --language python --image local-task-fuzz --out /tmp/task-fuzz-smoke
```

Replace the digest placeholder; mutable tags are rejected. Local `sha256:...`
image IDs also work. The build wrapper creates a minimal context, records exact
recipe/dependency-lock/log hashes and the resulting image identity, and verifies
that Python/Node/Go/default Rust versions match the parent image. It does not
replace a task interpreter with an easier reference ABI. OCI image bytes are
not promised bit-for-bit identical: tool/dependency identities and consumed
source/build inputs are what receipts bind.

For daemon-local `sha256:` config IDs, the wrapper binds a retained local tag to
the full requested ID, verifies it before/after, and explicitly uses that same
daemon's legacy builder (`DOCKER_BUILDKIT=0`). Raw config IDs are **not** passed
as Dockerfile repository names. A remote BuildKit worker cannot safely resolve a
daemon-local image. If the daemon no longer supports this local builder mode,
the result is unsupported; use an authenticated OCI registry **manifest digest**
as the explicit distribution-only route, never another task image.

Five recipes under `images/` have pinned reference parents for calibration.
Those generic references **do not qualify real task candidates**. A task maintainer
must build the overlay on its actual pinned task image, preserve features and
apply identical instrumentation to separately built pristine/candidate arms.
Unsupported parent distributions, platforms, ABIs or components produce an
explicit build failure/unsupported receipt, not a fallback to another task
toolchain.

Task images commonly disable package indexes after building their original
dependencies. Overlay bootstrap therefore overrides `PIP_NO_INDEX`,
`CARGO_NET_OFFLINE`, `npm_config_offline`, and Go's task-only module proxy
**only for the locked installation command**. Python/Node/Rust offline runtime
settings remain intact; Go retains its parent runtime proxy configuration.
This does not permit network access in isolated judge workers.
The Python venv includes the original task's system site packages, so pinned
runtime dependencies such as SQLFluff's `tblib` remain visible; locked fuzz tools
are installed into the venv with hash verification and `--ignore-installed`.
Task-specific import/dependency smoke is still required, not inferred from the
generic fixtures. Custom parent virtualenv/editable import setups need their own
preservation check rather than a silent dependency substitution.
The native reference recipe rejects non-Debian-bookworm parents before touching
apt sources; Ubuntu tasks must use a compatible task-owned instrumented recipe.

| Layer | Locked tooling | Evidence / limitations |
|---|---|---|
| C/C++ | Debian snapshot 20261025, Clang/LLVM 16.0.6, AFL++ 4.04c | libFuzzer/AFL++, LLVM line/branch exports, ASan/UBSan/TSan controls; MSan fixture is not an instrumented dependency closure |
| Rust | Task stable default; explicit nightly-2026-10-01; cargo-fuzz 0.13.2; proptest 1.12.0 | Matching LLVM tools, nightly branch coverage; sanitizers/Miri require separate feasibility and dependency/std builds |
| Go | Task compiler, `GOTOOLCHAIN=local`; rapid 1.2.0 | Native fuzz, race/checkptr, block exports; ordinary Go profiles **do not contain branch outcomes** |
| Python | Task interpreter; Hypothesis 6.170.12, Atheris 3.1.0, coverage.py 7.17.0 | Hash-locked Linux x86_64 wheels for CPython 3.12/3.13/3.14; branch data and independent child startup/merge |
| JavaScript | Task Node; fast-check 4.10.2, Jazzer.js 4.0.0, Istanbul/source-map libraries | Task-code statement/branch instrumentation and remapping, not VM-only/function-only coverage |

`toolchains.lock.json`, `python.lock`, npm's integrity-bearing package lock,
Cargo's checksum-bearing lock and Go sums bind dependencies. Native/JS snapshot
packages retain `/opt/fuzz-os-packages.lock`. The Rust recipe additionally
verifies the cargo-fuzz crate digest and complete dated nightly manifest hash.
The nightly manifest contains component archive identities, including Miri.
Rustup self-update is disabled.

### Compatible Jazzer without changing libc

The published Jazzer 4 native addon requires GLIBC_2.38 and fails in the pinned
Node 22 bookworm reference (glibc 2.36). That failed invocation is not a pass.
The optional `javascript-source` overlay rebuilds the addon from a hash-pinned
upstream source archive and upstream hash-pinned patched compiler-rt, **on the
same task runner libc and Node**:

```sh
python -m tools.fuzz_runners.build_image \
  --language javascript-source --task-image 'sha256:IMMUTABLE_JS_RUNNER_ID' \
  --tag local-js-fuzz-compatible --out /tmp/js-source-image
python -m tools.fuzz_runners.smoke --language javascript \
  --image local-js-fuzz-compatible --out /tmp/js-source-smoke
```

This nested image record binds its parent runner record, which binds the actual
task image. Do not substitute a newer distro/Node image to hide the failure.
The recipe and fixture are Linux x86_64; other targets need their own trial.
Native task addons still require their own sanitizer applicability/closure
evidence; validating the fuzz engine is not that evidence.

### Rust standard-library sanitizers and Miri

Plain `rustc -Zsanitizer=thread` is rejected by the pinned nightly for ABI mismatch
against its unsanitized standard library. Never use
`unsafe-allow-abi-mismatch` to suppress that check. The optional
`rust-sanitizers` overlay installs matching Miri, seeds locked standard-library
dependencies with `-Zbuild-std`, and prepares a matching Miri sysroot:

```sh
python -m tools.fuzz_runners.build_image \
  --language rust-sanitizers --task-image 'sha256:IMMUTABLE_RUST_RUNNER_ID' \
  --tag local-rust-instrumented --out /tmp/rust-instrumented-image
python -m tools.fuzz_runners.smoke --language rust --extended-rust \
  --image local-rust-instrumented --out /tmp/rust-instrumented-smoke
```

The fixture validates a correctly synchronized program, a real Rust data race,
safe Miri execution and a one-past-end UB control. Task-specific unsafe APIs,
FFI/syscalls and dependency features may remain unsupported. Do not relabel
those as passed because a small synthetic fixture works.

## Engine and coverage interfaces

`plans.build_plan(language, instrumentation)` returns separate compiler flags,
environment, ordered export commands and explicit limitations.
Rust `coverage` is the task compiler's line-coverage mode; explicitly choose
`branch-coverage` for the separate pinned-nightly build with
`-Zcoverage-options=branch`. A stable line-only build is not branch evidence.
`plans.engine_spec(...)` returns argv, seed environment, timeout and replay
limitations. It does not build a shell command or load both comparison arms in
one process. The controller supplies its isolated worker transport and budgets.
Property adapters must actually consume their declared seed/example options.
Go's native fuzzer has no stable user RNG-seed flag: freeze and replay the actual
corpus/order rather than asserting bitwise reproducibility from a primary seed.

The shared `DockerExecutor` runs every capability/header worker as UID 10000,
network-disabled, with dropped capabilities and the existing process isolation.
It is not replaced by an independent task runner. Trusted runner code is mounted
read-only; capability receipts hash runtime source/fixture inputs before and
after execution and reject changes. Raw stdout/stderr, argv, environments and
timeouts are retained. Fixture findings are never candidate regressions.

Normalize exports **after** collecting every expected worker/child:

```sh
python -m tools.fuzz_runners.coverage \
  --format llvm --input llvm.json --source-root /app/task-source \
  --output normalized.json
```

Supported formats:

- `llvm`: `llvm-cov export` JSON; merge all child `%p-%m.profraw` files first
  with **matching-toolchain** llvm-profdata. Keep expansions/features and all
  linked task objects. Missing branch inventory/unexported task macros is
  unsupported, not a zero denominator.
- `gcov`: GCC JSON export format1/2 (including gzip) from
  `gcov --json-format --branch-probabilities --branch-counts`, using the task's
  exact GCC/gcov build. Keep zero branch edges and partial-line diagnostics.
  Missing per-line branch inventories stay unsupported. This allows a pinned
  GCC 15/trixie task to retain its compiler instead of replacing it with LLVM 16.
  Canonical source-bound changed-target inventory still owns denominators.
- `python`: `coverage json` after combining parallel data; `--branch` and
  per-file executed/missing branch inventories are mandatory. The venv `.pth`
  starts coverage when `COVERAGE_PROCESS_START` is set. Hardcoded child
  interpreters that bypass that hook need explicit startup and worker receipts.
- `istanbul`: run `remap.cjs input.json output.json` first, or
  `remap.cjs --merge output.json worker1.json worker2.json ...`.
  Instrument task code before import. Original locations are source-mapped;
  an implicit `if` false edge retains its actual counter at the condition's
  mapped location, even when Istanbul represents its else span as empty.
  Unresolved source URLs/maps are unsupported.
- `go`: native coverage blocks are retained, **not** expanded into guessed
  executable lines or branches. Supply a source-bound executable-line inventory
  and `--go-branches` explicit edge inventory/counters, including zero outcomes.
  `--path-map 'module/import/prefix=/app/task-source'` maps module names; it never
  guesses by basename. `go/branches.go` is a concurrency-safe counter utility,
  not a complete AST instrumenter or evidence that all branches were inserted.

Line/branch records preserve file/range/outcome identities and raw counters.
`available` means the export format was collected, **not** that a candidate
met adequacy. The controller must join the trusted source-bound changed-target
inventory and require every applicable feature/worker. It owns thresholds and
approved exclusions; collectors never report self-authorized coverage fractions.
Out-of-source files are explicitly excluded.

`receipt.build_receipt(...)` binds actual source/build/image/parent/features,
tool identities, flags and capabilities. `receipt.coverage_receipt(...)` binds
raw and normalized artifact hashes and expected/observed worker inventories;
missing children remain unsupported. These helper schemas are not private
policy alternatives. The shared integrator authenticates/adapts them into the
canonical fuzz receipt.

## Two-TU public-header consumer matrix

`header_matrix.validate_manifest`, `run_cell`, `compare_cells` and
`header_run.run_pair` provide compile/link evidence. Schema:
`optimbench-header-matrix-v1` with frozen `required_cells` and one record per
header/legal-include-context/dialect/feature/platform cell. Each supported cell
supplies:

- Source-bound upstream `support_refs` with quote, source path/URL and SHA256.
- Header inventory, supported dialect/language, features, macros and include
  directories; legal include context in **both** consumers.
- Exactly two distinct TU contents, actual public symbol references, matching
  library paths and pinned compiler/linker configuration.

The runner checks reference/hash **shape**, not the truth of an upstream support
claim. Task maintainers must authenticate support and completeness
upfront. Accepting `c17` as a syntax token does not claim a task supports it,
and a task whose upstream documents a C17 requirement must not be lowered to C11. Unsupported
cells need reasons/evidence and issue no compile commands.

Unresolved support is **not** documented unsupported. Retain an explicit frozen
cell with `support_status: "pending"`, `supported: null`, known `headers`,
`reason` and partial source-bound `support_refs`. It runs no compiler and emits
`support_evidence_pending`, not a pass/unsupported exclusion or candidate finding.
It remains in `required_cells` until independent upstream authority resolves it.

For a real pinned upstream **public consumer/configure/CI recipe that uses
default flags**, a cell may instead declare `dialect: "compiler-default"` and
`support_basis: "pinned-upstream-default-flags"` with authenticated source refs.
The runner adds **no `-std` flag**, records the actual compiler/flags and
predefines (`__STDC_VERSION__`, `__cplusplus`, inline-model macros, etc.), then
performs both independent compiles and link. Macro values are observations:
they do not establish a numeric minimum, portable support for all standards,
or GNU89 support. Compiler acceptance alone is not upstream authority.
If default-flag consumer evidence is unresolved, retain a pending cell instead.

### Optional third-party SDKs missing in pristine

This is narrowly represented as raw `missing_third_party_sdk` attribution, not
an automatic task verdict. A frozen supported cell must explicitly declare
`optional_third_party_sdk_headers: [{"header": "vendor/sdk.h", "support_refs":
[{"path": "pinned/upstream/source", "sha256": "...", "quote": "..."}]}]`.
The canonical controller authenticates the SDK's optional/third-party status
and the legal include context; the runner never guesses that any missing
project or standard-library header is an optional SDK.

Only these cells add the diagnostic-only GCC/Clang `-H` flag. For every failed
pristine TU, the complete hash-verified stderr must contain exactly one error:
an actual missing-include fatal diagnostic. Its real include trace must pass
through a requested installed public header; its authenticated source line
must literally include the declared SDK header. Proof retains exact missing
name, diagnostic, line/column, source-line bytes, source/include-chain hashes,
TU/cell/compiler bindings and compiler argv/stderr commitments.

Missing primary headers, a TU's missing context include, unknown dependencies,
syntax or other errors, truncated diagnostics, missing tools and infrastructure
failures do not receive this attribution. Pristine success plus candidate
compile/link failure remains `candidate_compatibility_difference`, including
when only the candidate is missing an SDK. Unknown pristine failures retain
`baseline_supported_cell_failed`. Pending language/dialect support (including
unresolved NumPy C++ scope) remains pending, never inferred SDK N/A.
The shared controller alone maps positive pristine evidence to scoped N/A.

Invoke `python -m tools.fuzz_runners.header_run --help` for the two-arm CLI.
Both arms use the exact same immutable runner image and frozen manifest, with
separate read-only matching build mounts. Receipts contain compiler/linker
identities, full canonical `cell_sha256`, both header/library/TU hashes, compile
argv/exits/logs/object hashes for **each** TU, undefined-symbol tables, and a
separate executable-link outcome/hash. Source/build commitments are supplied
by the authenticated controller; exact consumed header/library bytes are also
hashed here.

LTO, changed inline semantics, multiple-definition allowances, linker GC,
undefined-symbol bypasses and output/stage overrides are rejected. Different
cell/toolchain bindings are `invalid_run` evidence. Pristine supported-cell
compile/link failure is `baseline_supported_cell_failed`, not a candidate pass.
Pristine success plus candidate failure is
`candidate_compatibility_difference`; the canonical controller attributes the
contract verdict. No task-wide pass is issued by this utility.

Mandatory runner calibration:

```sh
python -m tools.fuzz_runners.header_control \
  --image 'sha256:IMMUTABLE_NATIVE_RUNNER_ID' \
  --parent-image 'repository@sha256:PINNED_PARENT_DIGEST' \
  --out /tmp/header-control
```

The disposable static-inline pristine header links in all three declared fixture
cells. Its independently authored GNU-inline mutant compiles both TUs but fails
the GNU89 link with duplicate definitions, while C99/C++11 link. A declared
unsupported cell stays unsupported. This is **not** proof that jq or any
task supports GNU89. Separate validation exercises umbrella legality, C++
linkage, supported-pristine failures and wrong-cell defenses.

## Calibration evidence and limits

Compact manifests, image identities, source/compiled-artifact commitments,
raw logs/exports and normalized coverage are retained under
`tools/fuzz_runners/receipts/20261026/`. Reproduce with the pinned recipes and
`smoke.py`; package new observations with `collect_receipts.py`.

Runtime controls validated native libFuzzer/AFL++/ASan/UBSan/TSan/LLVM coverage,
Rust cargo-fuzz/proptest/ASan/nightly branch coverage/rebuilt-std TSan/Miri,
Go fuzz/rapid/race/checkptr plus explicit edge counter concurrency, Python
Hypothesis/Atheris/branch and child coverage on **both CPython 3.12 and 3.14**,
and fast-check/Jazzer/source-mapped Istanbul on the pinned Node 22 runtime.
The original incompatible Jazzer addon and unsanitized Rust TSan invocation are
explicitly diagnosed, not passed.

Remaining task-level obligations include MSan's complete dependency closure,
native Python/JS module instrumentation where applicable, a source-complete Go
edge instrumenter/inventory, complete public surfaces, candidate changed-line/
branch denominators, all-worker/feature campaigns, races/lifecycle/resource
checks and minimum mutation scores. No actual task/candidate is qualified by
these synthetic controls. Memory caps/ratios are not enforced here.
