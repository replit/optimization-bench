# Scoring host setup and early preflight

Score on your own Intel x86-64 Linux machine matching the documented scoring
configuration: 8 dedicated judge CPUs, 16 GiB of memory for the task, SMT off
(one thread per core), Docker with Buildx, and spare CPUs for the system outside
the judge set. The reference configuration recorded in
`judge/hosts/registry.json` uses judge CPUs 0-7 and system CPUs 8-15. No cloud
account or particular provider is required; see [judge.md](judge.md) for what
`claimable` means.

Run setup **before starting a quiet scoring session**, never next to a timed
run. `tools/setup_scoring_host.sh` is the single setup script.

**The initial clone must skip LFS smudging**, or Git can hydrate the entire
multi-GB corpus before any setup/preflight code can run:

```sh
sudo apt-get update && sudo apt-get install -y git git-lfs
GIT_LFS_SKIP_SMUDGE=1 git clone "$REPOSITORY_URL" verifier  # the OptimizationBench repository
git -C verifier lfs install --local --skip-smudge
```

From an existing checkout, `sh tools/clone_verifier_checkout.sh REPOSITORY_URL DESTINATION`
enforces this bootstrap pattern. It never pulls LFS objects. Later public and
authorized retained-heldout acquisition is scoped to exact task paths.

On a fresh machine, provision it once before setup.
`tools/provision_scoring_host.sh` installs Docker CE and the host tools,
disables background timers and reserves CPUs 8 and up for the system (systemd's
manager, Docker and containerd run there through `CPUAffinity`) and applies the
Docker MTU/MSS repair described below; rerun that repair after every reboot or
Docker restart. Run the judge
itself under `taskset -c 0-7`. The judge records both CPU sets, and a score is
claimable only when they are 0-7 and 8-15:

```sh
cd verifier
sudo bash tools/provision_scoring_host.sh
sudo JUDGE_VENV=/opt/judge sh tools/setup_scoring_host.sh
sudo /opt/judge/bin/python -m tools.scoring_host check --task sqlglot-mozilla-transpile --stage-public
```

Choose a writable `JUDGE_VENV` if `/opt/judge` is not writable. Setup creates that
venv and installs `judge/requirements-interface.lock` plus the complete
`judge/requirements-host-registry.lock` schema dependency closure, with
`--require-hashes --only-binary=:all:`. The judge must use its Python, not a
system interpreter with unrelated packages. Preflight imports/version-checks
every locked package and initializes all configured grammars plus the registry's
Draft 2020-12 schema validator. The interface lock pins nine parser packages.
These parsers belong on the host, never in agent task images.

`judge/host-runtime.lock.json` explicitly supports standard stable CPython
**3.12 and 3.14**, with a dedicated venv and the locked wheel ABI (not a
free-threaded interpreter). Bootstrap checks the interpreter before installing
packages; preflight checks it before parsing dependencies or comparing an audit.
An unsupported version gets a `host-interpreter` error and concrete
`JUDGE_PYTHON=python3.12 JUDGE_VENV=/opt/judge-py312 sh tools/setup_scoring_host.sh`
instructions, not a misleading external-command audit failure.

The registry closure pins jsonschema 4.19.2, attrs 25.4.0,
jsonschema-specifications 2025.9.1, referencing 0.37.0, rpds-py 0.30.0 and
typing-extensions 4.15.0. Every supported wheel hash is committed; typing
extensions is installed on both minors for an identical declared closure.
Maintainers may regenerate/review this separate lock with
`python3 tools/lock_host_registry_dependencies.py`; setup/preflight never resolve
or relock dependencies dynamically.

Read-only `check` fails with explicit commands for missing `python3-venv`,
GNU `nm` **and `c++filt`** from binutils, Git/Git LFS, Docker, `buildx`, `iproute2`, `iptables` and
conditionally `ffmpeg` for selected public PNG/RGB24 acquisition. It checks
local Git LFS configuration, reachable Docker/cgroup v2, readable
CPU/memory/PID/cpuset controllers and Docker bridge network state.
It does **not** prove the task image works, the host is quiet, or the host is a
registered scoring machine.

## Docker/buildx and the MTU/MSS repair

Install the Docker daemon through your distro setup before reservation.
Missing `buildx` has a hash-pinned Linux amd64/arm64 installer:

```sh
python3 -m tools.scoring_host install-buildx
docker buildx version
```

It installs Buildx v0.24.0 into the active Docker CLI plugin directory, verifies
the release SHA256, is idempotent for that exact binary, and refuses to overwrite
a different operator-managed plugin or a symlink. It neither starts a builder
nor replaces Docker. Other architectures require an explicitly reviewed plugin.

Network repair is a separate, **explicitly mutating** quiet-host action:

```sh
sudo python3 -m tools.scoring_host fix-network --mtu 1400 --confirm-no-timing-session
```

It requires a local Unix-socket Docker daemon and root/CAP_NET_ADMIN. It
enumerates actual Docker bridge interfaces, lowers MTUs only when larger than
the declared maximum, and adds checked, commented TCPMSS
`--clamp-mss-to-pmtu` rules in each bridge's incoming/outgoing direction.
IPv6-enabled bridges also receive ip6tables rules. Repeated execution does not
append existing rules or raise an already smaller MTU; rule-read permission
errors are not treated as an absent rule.

The repair does **not** restart Docker, edit daemon configuration, disable
firewalls, alter non-Docker interfaces, or claim to be persistent across reboot.
Rerun it after host/daemon restarts or creation of another bridge; preflight
checks the live state again. Serialize setup commands. This structural
check is not a substitute for a real network transfer; a completed public
payload acquisition provides its own size/hash evidence.

## Public inputs: verify real bodies before differential generation

An image's cache is not the verifier checkout's public corpus. The shared
helper checks and materializes missing fetch-derived public files before any
generator opens them:

```sh
/opt/judge/bin/python -m judge.public_input_preflight \
  --task sqlglot-mozilla-transpile --fetch --out /tmp/sqlglot-public-inputs.json
```

Without `--fetch`, this is a read-only readiness check. With it, the helper calls
`tools.stage_payload.stage(task, fetch=True, scope="public")` **as a function**,
then authenticates every real public file/history again. It never runs the
catalog-writing staging CLI. Failed acquisition rolls back its batch.

Rules:

- Only explicit `scope: public` entries under the task environment are staged.
  Paths/symlinks, exact bytes/SHA256 or authenticated Git-history inventories
  must verify. Merely having a fetch recipe is not readiness.
- Held-out fetch-only bodies remain outside the repository. No held-out contents
  are fetched, copied from the image, or used to generate cases.
- LFS pointer text is never accepted as an input body. Setup runs
  `git lfs install --local --skip-smudge`, never global smudge/pull. Missing
  public LFS sources produce exact `--include=<public paths> --exclude=''`
  pull/checkout commands; no private/task-wide/global pull is performed.
- Payload manifests are SHA-bound and must not change during staging. Expected
  outputs/manifests are never regenerated to admit new input data.

Integration hooks used by the shared judge:

```python
from tools.scoring_host import scoring_preflight
from judge.public_input_preflight import ensure_public_inputs, needs_ffmpeg

host = scoring_preflight(require_ffmpeg=needs_ffmpeg(task))
public = ensure_public_inputs(task, fetch=True)
# Only now: differential.harness(task), generate(...), source/behavior gates.
```

Call host preflight at the top of scoring before admission/build/samples. Call
the public helper **before loading/invoking the generator**, including direct
differential entry points; a scored path may call it earlier and reuse the
passing receipt until its inputs change. These helpers do not assign candidate
correctness or performance verdicts. `PreflightError`/`PublicInputsError` mean
invalid infrastructure, not candidate failure.

## External-command audit

`tools/judge_external_commands.json` inventories launch sites/`Step` producers,
literal binary names, explicit dynamic producers, task-configured worker
commands and source hashes. It covers judge modules, shared runner, public
payload staging/acquisition, differential generators, task drivers and setup.

Host control-plane dependencies include Docker, GNU nm/c++filt, Git/LFS and the locked
Python venv; optional public RGB24 conversion uses host ffmpeg. Node/Go/C
compilers, build scripts, generated task programs, task-configured argv and
their descendants normally run in **pinned worker images**, not on the scoring
host. Explicit LocalExecutor/direct C-preprocessor modes are separately marked,
not silently substituted for an image's pinned toolchain.
Native-surface projection specifically requires `c++filt -n`; preflight checks
a known Itanium function/vtable and unchanged invalid/unmangled names. Missing/incompatible
demangling fails with the binutils installation command rather than dropping
unproven weak symbols or treating them as absent.

Dynamic argv remains visible rather than inventing a complete static binary
list. Pinned-image build/test/process controls must exercise those dependencies;
this audit is not a successful-execution claim for arbitrary candidate children.
No raw shell scripts, endpoint configurations or credentials are dumped in the
manifest.

## Task-scoped staging and catalog maintenance

`stage_payload.py --task NAME --fetch` changes that task's requested payload
scope only and never calls catalog generation. Its receipt explicitly says
`catalog_refreshed: false`; failed acquisition rolls back its batch.

Catalog maintenance/checks have an explicit task subset:

```sh
python3 tools/update_catalog.py --task sqlglot-mozilla-transpile --check
python3 tools/update_catalog.py --task sqlglot-mozilla-transpile --refresh-bindings
python3 tools/check_tasks.py --task sqlglot-mozilla-transpile --require-ready
```

The task subset propagates through distribution normalization, binding refresh
and generation. It never rewrites another task's provenance, global README or
dataset selections. Prior global collision disclosures are preserved, not
removed by a local check. Repository-wide rewriting requires explicit `--all`;
the staging path never requests it.

Unfetched/missing LFS objects are per-task `unavailable` rows, not JSON parsing
exceptions or an excuse to bind pointer text. Unavailable tasks are not rewritten.
An unavailable **requested** scope exits 2 after structured reporting, never
claims success, and does not stop unrelated materialized tasks from being checked.
Real materialized byte/SHA corruption or malformed JSON remains fatal; it is
not relabeled missing/unsupported.

Cross-task leak checks still cover materialized packages and explicitly report
`complete: false` plus unavailable task names when the audit is partial. An
unavailable task's safety is not certified. Full-repository maintenance
must finish the complete audit before making a repository-wide claim.

**Git LFS is required for retained held-out inputs and expected-output bodies,**
not only public recovery packets. On a verifier checkout, initialize
`git lfs install --local --skip-smudge`, then recover only the exact listed
retained paths with `git lfs pull --include='tasks/NAME/tests/heldout/EXACT_PATH' --exclude=''`
and `git lfs checkout 'tasks/NAME/tests/heldout/EXACT_PATH'`. Never materialize
fetch-only held-out reference bodies in the repository or agent image; those
continue to use verifier acquisition outside the checkout. No global LFS pull
is an implicit setup/staging/check action.

After producers or dependencies change, review them and refresh this audit
explicitly:

```sh
python3 -m tools.judge_command_audit --check
python3 -m tools.judge_command_audit --write --reviewed
python3 -m tools.judge_command_audit --check
```

Preflight never silently regenerates the audit. Source changes after review
invalidate it. This source-specific audit refresh is separate from task catalog
generation and never changes task manifests/provenance.
Argument identities use exact UTF-8/LF source segments, not `ast.dump` or other
interpreter-version-specific AST reprs. Identical checked-out source therefore
produces the same audit on both supported Python minors. A new source commit
still requires one reviewed manifest refresh; do not locally relock a
different source tree just to make a host pass.
