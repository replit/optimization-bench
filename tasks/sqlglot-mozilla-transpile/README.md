# Convert, optimize, trace lineage and compare SQL

One image runs four equally weighted cold batches using the same rebuilt SQLGlot package: 284 public Mozilla SQL conversions, 125 schema-aware optimizer requests, 125 column-lineage queries and 99 structural-diff pairs. Every distinct input runs once per unit. Complete SQL, typed trees, lineage graphs and structural edits are checked.

## Contract

Byte-identical complete per-query output digests and preserved public APIs, errors and warnings; untouched upstream tests keep baseline outcomes; no input/evaluator detection, precomputation, delegation, persistent caches or leftover processes.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 633.
- Held-out inputs: in repository.
- Input bodies stored in Git: 912,983 bytes.
- Input bodies acquired from authenticated upstreams before image build: 532,813 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task sqlglot-mozilla-transpile
python3 tools/stage_payload.py --task sqlglot-mozilla-transpile --fetch
python3 -m judge.public_input_preflight --task sqlglot-mozilla-transpile --fetch
python3 -m tools.public_runtime_packet --task sqlglot-mozilla-transpile \
  --cache /absolute/trusted-cache --out /absolute/new-sqlglot-mozilla-transpile-agent-packet
```

The packet declaration defines the exact read-only runtime input roots.
Acquisition archives are not implicitly visible to the agent. An agent run
mounts the packet read-only; the source is then exported and scored separately. See
[public runtime packets](../../docs/public-runtime-packets.md).

On a hosted sandbox that cannot mount these inputs, run
`python3 tools/fetch_public_inputs.py --task sqlglot-mozilla-transpile` from a temporary checkout
inside the container before the agent starts, then delete the checkout; see
[Running an agent](../../README.md#running-an-agent).

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out assets are available via Git LFS and are authenticated at scoring.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
