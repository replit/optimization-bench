# Optional public runtime packets

Only tasks declaring `environment/public-input-packet.json` use this bridge.
Ordinary image-contained inputs, licensed project trees and required empty
projects retain their original paths and need no new declaration or wrapper.

Use CPython 3.12 or 3.14, Docker with Buildx, Git/Git LFS, the declared host
compiler tools and the task's original resources. Native qualification needs
the original eight CPUs and 16 GiB; a configuration or controller unit test is
not native admission.

## Prepare the original public data

For an input-free image whose original visible root was `inputs/files`:

```json
{"schema":"optimbench-public-input-packet-v1","mounts":["inputs/files"]}
```

Mounts are workload-relative and bind read-only at the same original
`/app/workload/` path. Commit the declaration as protected image context and
rebuild/rebind the image through the canonical recipe. Existing images carrying
the old recipe label are not relabeled as the new recipe.

```sh
python3 -m judge.public_input_preflight --task TASK --fetch
python3 -m tools.public_runtime_packet --task TASK --image ORIGINAL_IMAGE \
  --cache /absolute/trusted-cache --out /absolute/new-agent-packet
cat /absolute/new-agent-packet/agent-command.txt
```

The preflight authenticates the existing public input commitments. A corrupt
source cache fails instead of silently reacquiring a replacement. Acquisition
archives are not implicitly runtime inputs.

For a protected original derivation, declare its original output byte counts
and SHA-256 values under `derived_inputs`, plus the original digest-pinned
preparation image, argv and exact output parent directories under `prepare`.
Preparation uses no network, a read-only root and environment, and writable
binds only for those declared output directories. Every derived output must
authenticate before its runtime root is exposed. A task therefore exposes
only its declared data roots and authenticated derived outputs, never raw
acquisition inputs. The original protected recipe/helper/guard must remain unchanged.

The helper emits `packet.json` and exact read-only Docker mount arguments; use
those mounts when you start the agent container (see
[agent-runner.md](agent-runner.md)). For compatibility it also writes a local
task wrapper for the legacy Harbor harness, which is no longer used. The wrapper changes only the image reference;
original task resources, networking, artifacts, source contract and prompt are
preserved. It adds no profiling or security privileges. The generated Harbor
command explicitly disables its verifier and is **agent-only**, not a
correctness, performance, cold-network or qualification claim.

Generated task inputs are contained, with an exact copy of the original prompt.
The task's original tests, solution, data and other root files are not copied or
linked into the wrapper. Its small contained verifier always exits 2, awards no
reward, and directs users to the separate canonical judge if verification is
accidentally enabled. Harbor's path-containment checks remain enabled.
The generic agent command is still an unvalidated configuration template:
passing task validation does not establish provider setup or agent startup.

## Replay the task's existing source contract

Export only the task's admitted production source roots using its existing
source contract. The canonical artifact descriptor supports paths relative
to the descriptor, including wrapped multi-root maps. Scoring and both
dedicated/direct verifier CLIs resolve these paths independently of the
caller's working directory.

On the separate judging host, retain the original policy:

```sh
python3 -m judge.heldout_check --task tasks/TASK --image ORIGINAL_IMAGE \
  --cpuset ORIGINAL_EIGHT_CPU_SET --artifacts /absolute/submission.json \
  --output /absolute/heldout-result.json
python3 -m judge.score --task TASK --image ORIGINAL_IMAGE --separate-sandbox \
  --artifacts /absolute/submission.json --out /absolute/new-score
```

Candidate upstream rejection is incorrect and retains its rejection receipt.
Pristine baseline qualification and infrastructure failures remain invalid;
neither status proves that the corpus comparator ran. A failing upstream
negative control is not promoted to a successful corpus control.

Host registration is separate from provisional numeric interpretation.
An unregistered host is not officially claimable. No component test, prepared
packet or correct-source no-op proves a resolved speedup or passing A/A quality.

This component does not publish or certify Git/source-export tooling or a
clean-bootstrap proof.
