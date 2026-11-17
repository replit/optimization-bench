# Ruff 0.3.0 preparation and separate verification

Version 0.3.0 preserves the two original public operations and introduces the
separate `ruff-heldout-0.3.0-httpx-rich` edition. Its expectations are newly
authored; they are not recovered original 0.2.0 held-out results. The unavailable
0.2.0 identities and recovery status remain recorded in provenance.

## Requirements and acquisition

Use Linux x86-64, Docker, Git and final CPython 3.12 or 3.14. The task requires
eight schedulable CPUs and 16 GiB for its containers, plus host/Docker overhead;
32 GiB host RAM is recommended for source builds and upstream tests. Allow at
least 40 GiB of free disk for the image, separate pristine/candidate builds and
Cargo test cache. Other architectures and emulated timing are not supported.

Image construction executes the pristine Ruff binary; it is native workload
execution, not a source-only setup probe. A successful local build or run does
not qualify a host for timed scoring.

From the repository root:

```sh
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools
python3 tools/check_tasks.py --task ruff-project-lint --require-ready
docker build -t optimbench:ruff-project-lint tasks/ruff-project-lint/environment
docker run --rm --network none optimbench:ruff-project-lint /app/baseline-build/run
```

The Docker context is only `environment/`. Image construction anonymously
downloads the SHA-256-pinned Ruff and Rust archives, fetches the complete pinned
pandas/FastAPI Git trees, verifies every original file, and vendors Cargo.lock's
dependencies. The original fat-LTO release profile is unchanged. Rebuilds and
all workload execution are offline. No private account, prebuilt private image,
registry credential or employee filesystem is needed. Preserve the licenses
listed in [notices/](../notices/).

There are exactly two public timed units, `pandas` and `fastapi`, not one unit per
source file. Their complete configured trees, frozen disjoint CLI batches,
eight Rayon threads, `--no-cache --no-fix`, and byte-exact `cli-output-v1`
commitments are unchanged. No public correctness-only workload was removed.
Image-local output checks have no timer and are not performance qualification.

## Real source export and untimed control

The following exports an unchanged control candidate through the same twenty
source-root interface used for actual submissions. Edit only the production
files allowed by `release.json`; keep original tests and documentation comments.
Do not export a prebuilt executable as a candidate.

```sh
work="$(mktemp -d)"
cid="$(docker create optimbench:ruff-project-lint)"
docker cp "$cid:/app/ruff" "$work/source"
docker rm "$cid"
python3 - "$work" <<'PY'
import json
from pathlib import Path
import sys
work = Path(sys.argv[1]).resolve()
roots = json.loads(Path("tasks/ruff-project-lint/release.json").read_text())["source_policy"]["source_roots"]
(work / "artifacts.json").write_text(json.dumps({
    root: str(work / "source" / root) for root in roots
}) + "\n")
PY
python3 -m judge.score --task ruff-project-lint \
  --artifacts "$work/artifacts.json" --image optimbench:ruff-project-lint \
  --mode provisional --separate-sandbox --gates-only \
  --out "$work/public-control"
```

Run this on a separate evaluation host, not alongside an active optimization
agent. The shared judge admits the exported source, rebuilds pristine/candidate
code with protected assets, runs the original upstream identity/outcome suite,
checks public outputs/process state, executes diagnostic-producing small/large
CLI stdout-fault cases, and checks the configured 1/2/4/8 CPU correctness policy.
The CLI fault controls include healthy output, `/dev/full`, closed readers and
closed stdout; input bytes are watched. They are untimed verifier controls, not
replacements for either public project operation.

The protected public surface is the standalone `--bin ruff` executable. The
in-process stream-callback, aliasing, persistent-library-session and C/C++ header
gates record explicit source/recipe-bound non-applicability; they do not invent
a linkable Rust/C API or award execution credit to a stub. CLI behavior and the
original internal Rust API tests remain required.

Inspect `result.json`, `summary.txt`, per-gate receipts and exit status together.
An untimed run has `claimable: false` and no speedup. Do not pass `--gate-profile
legacy` to bypass current gates. On a host with fewer than eight available CPUs,
the required affinity checks remain unavailable rather than being waived.

For provisional paired timing, use a fresh `--out` and omit `--gates-only`.
The unchanged task sampling policy, one-second unit floor, current A/A checks
and host-noise checks still apply. An unregistered self-hosted result is
diagnostic, never an official performance certificate. No historical receipt
qualifies this restored package, and no timing success is promised for the
original short CLI operations.

## New held-out edition

The new edition has two complete configured projects: HTTPX 0.28.1 at
`26d48e0634e6ee9cdc0533996db289ce4b430177`, and Rich 13.9.4 at
`43d3b04725ab9731727fb1126e35980c62f32377`. Each case invokes exactly
`ruff check --no-cache --no-fix --output-format=json .` on a fresh complete
project copy, with the original eight Rayon threads and Git discovery context.
The original two public timed units, driver, build recipe, production source,
upstream expectations, gates and sampling policy remain unchanged.

`heldout.lock.json` binds both fetch-only Git histories, every case document,
the full member/mode inventories and the complete nonempty-file index.
`heldout/inputs.lock.json` indexes all 644 nonempty entries; the seven original
empty files remain in the complete 651-file projects. No project member was
pruned, normalized or newly exempted. Whole NetworkX and SQLAlchemy candidates
were rejected for public-input collisions before any Ruff execution.

The HTTPX BSD-3-Clause license and Rich MIT license were read at the exact pins.
Their complete original trees retain the verbatim licenses, copyright notices
and other fixtures. Source and license commitments are in
[`heldout/edition.json`](heldout/edition.json); project/Git bodies are fetched
externally, not redistributed in Git or copied into the agent image.

One sequential untimed pristine-baseline pass authored the new commitments,
after freezing corpus membership/bytes, full Git context, command, driver and
the already-built immutable image. HTTPX returns 0, including its complete
141-byte warning stream; Rich returns 1 with diagnostics. Both exact
`cli-output-v1` outputs are committed by length/SHA-256. No warning, diagnostic
or stream was dropped. The pass used four available CPUs with eight Rayon
threads, collected no timing samples, and verified owned-container cleanup.
It is not an eight-CPU correctness/qualification run or a performance result.

The shared verifier acquires and authenticates the declared histories in an
external view before execution. Do not populate `tests/heldout/projects` in
the source checkout. The two small authored case documents are committed as
plain source configuration and require no Git LFS acquisition.

For separate held-out correctness verification:

```sh
python3 -m judge.heldout_check \
  --task tasks/ruff-project-lint --image optimbench:ruff-project-lint \
  --cpuset 0-7 --output "$work/heldout-control.json"
```

Use the current shared evaluation foundation; the task-only patch does not
vendor or fork those shared modules. `status: available` means the new locked
references and expectations exist, not that every native gate or bootstrap
has been qualified. This release includes no receipt for a full clean
upstream/public/held-out validation or for an eight-CPU performance
qualification of this task (`qualification-status.json` records
`scored_qualification: false`). Never regenerate these expectations after a
candidate fails.

The original 0.2.0 case/command and Git-discovery bindings remain unavailable.
Its raw recovery archive and normal/mutation cross-checks were not imported,
substituted, pruned or relabelled as this edition. Scores must identify 0.3.0;
this is not a claim of comparability to an unrecovered 0.2.0 held-out workload.

`test.sh` is a compatibility wrapper that calls the same protected judge in a separate verifier image; score with `judge.score`.
The trusted host supplies its configuration; the agent receives no verifier
directory, held-out data, repository-root mount or Docker socket.
