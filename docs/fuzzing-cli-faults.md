# Untimed CLI OS-fault observation API

`tools.fuzz_runners.cli_faults.run_pair` returns **facts only**, never a candidate
verdict, universal exit code or private stderr normalization. The shared
controller owns pristine-relative compatibility and stderr classification.

```python
from tools.fuzz_runners.cli_faults import run_pair
from runner import Step

result = run_pair(
    step=Step(["{build}/bin/tool", "input.sql"], cwd=".", env={}, timeout=20),
    inputs=prepared_case_tree,  # verifier-owned valid nonempty public case
    baseline=ctx["baseline"], candidate=ctx["candidate"],
    image=ctx["image"], cpuset=ctx["cpuset"], out=ctx["out"] / "cli-small",
    task=ctx["task"], public_inputs=ctx["public_inputs"], projects=ctx["projects"],
    watch_inputs=["input.sql"], watch_outputs=["out"],
    output_dirs=["out"], required_outputs=["out/result.sql"],
    faults=("healthy", "dev_full", "closed_reader", "closed_stdout"),
    case_id="public-cli-small", payload_class="small",
)
```

The image must be an immutable image ID/repo digest. Baseline/candidate build
roots are read-only, separately mounted under `/app/build`; protected legacy
launcher aliases point to the same arm. Every arm/fault has a fresh shared
`DockerExecutor` container: UID 10000, network disabled, capabilities dropped,
no-new-privileges, PID/memory/timeout bounds, normal shared shim cleanup.
The prepared input tree is independently copied and hash-bound for each worker.
No optimizer patch or production source is read/edited.

`Step.argv`/environment may use `{build}` and `{work}` (`/scratch/work`).
Absolute prefixes matching either supplied build root map to the current arm,
never to the other arm. `cwd`, stdin and watched/output paths are relative to
the case tree. Credential environment keys are rejected; probes do not require
external-account access.

Fault names:

- `healthy`: normal captured stdout.
- `dev_full`: actual `/dev/full` stdout, whose writes fail ENOSPC.
- `closed_reader`: pipe stdout with its only reader closed before exec.
  EPIPE or SIGPIPE behavior is **program/runtime/pristine-specific**.
- `closed_stdout`: fd1 closed in the child after descriptor setup and before
  exec. It is not simulated by an observer-side failed `Popen`.
- `readonly_dir`: actual read-only bind mounts at the declared output directories
  (EROFS, not a chmod the program could undo). Missing `output_dirs` is unsupported,
  never an automatically passing cell. Use a CLI command that actually writes
  those outputs; unrelated stdout commands cannot establish applicability.

Normal inputs are copied by the UID 10000 observer from a read-only seed mount,
so they are owned by the CLI user rather than root-owned host-copy artifacts.
Readonly output subtrees are separately seeded/mounted and excluded from that
copy; the observer verifies their read-only filesystem flag before invocation.

Supply separately declared small and large **valid nonempty** cases. Small
buffered CLI output can expose flush-only errors; a large payload must produce
enough actual healthy output to exercise writing rather than merely a small
compressed result. The helper records input/healthy-output byte counts but does
not invent a syscall-path proof or assert that a fault was triggered by a
program that never writes.

Observer JSON is `/scratch/observer.json`, outside the child's faulted stdout.
The trusted observer adopts CLI orphans, stops descendants before collecting
final effects, and publishes JSON after they are gone. It retains:

- Actual exit code/signal, bounded stdout/stderr heads, raw stream artifact
  paths/lengths/SHA256 and the first nonempty diagnostic line.
- Exact invocation/environment, injected OS fault (not claimed observed errno),
  timeout/stream-budget/setup/cleanup facts.
- Per-arm `execution_affinity`: sorted real CPU IDs sampled by
  `os.sched_getaffinity(0)` inside the Docker observer, not echoed from the
  requested cpuset. It is included in the hash-bound original observer JSON.
  The child inherits this mask; the real 1/2/4/8 CPU control independently reads
  the child's mask and retains a candidate-wrong-only-at-one-CPU raw control.
  CPU-set acceptance and correctness rejection remain central-controller policy.
- Watched input/output trees before and after, hashes/modes, missing required
  outputs, symlinks without following them, and permission changes from setup.

Pass a **durable** `out` (normally the canonical context output), not the case
input's `TemporaryDirectory`. Before returning, the host copies complete bounded
streams to `out/case-<id-hash>/<fault>/<arm>/{stdout,stderr}.bin`, verifies their
size and SHA-256 against the observer, and adds `row["stderr"]["artifact"]` /
`row["stdout"]["artifact"]` with absolute/relative path, root, size, SHA-256 and
`complete: true`. These copies survive worker/temp input cleanup. The original
`head_base64`/headline remain **previews only**; semantic normalizers must verify
and read the full retained artifact, never normalize a truncated head.
Missing or changed streams, and streams larger than 16 MiB, invalidate
retention rather than silently truncating evidence. No API can preserve evidence after the caller deletes its
explicit durable output directory.

Empty stderr is retained, not blanked or rejected. For example, pristine `/bin/cat`
terminates with SIGPIPE and empty stderr for the closed-reader control, but
reports ENOSPC/EBADF with nonzero exits for other controls. The runner does not
convert these into one universal expected code.

`status: observed` only means observation completed. It is **not pass**.
`invalid_run` means controller/transport/setup/timeout/stream-budget failure;
`unsupported` means an applicability/setup obligation is absent. Either remains
pending/invalid evidence for the canonical controller, never candidate success.
Input mutations, missing outputs, swallowed errors and changed signal/exit
behavior remain raw facts for pristine-relative classification.
