# Run sqlfmt through its CLI and native formatting API

**Two equally weighted timed workloads in one image.** The original CLI `batch` and frozen `native-batch` both count toward aggregate speedup. The public CLI batch keeps 89 of its original cold invocations. The native batch uses one cold process containing 844 public documents, with 2,470 documents from a separate origin for held-out evaluation. Each native document is formatted once, with every complete result and returned error retained. Both entry points use the same rebuilt source. Original CLI inputs and references remain unchanged. The native batch is scored, not an optional diagnostic. See the [native corpus and evidence](tests/fuzz/native/README.md).

## Contract

Byte-identical exit status, stdout, declared outputs and normalized stderr per invocation; upstream Go tests pass; no daemons or helpers outliving the invocation, no caches or extra files, no environment-driven behavior or input recognition, no subprocesses, no GC-setting changes; unsafe/syscall/runtime/debug/hash/crypto/compression/encoding imports forbidden; source growth <= 64 KB; peak RSS is measured and reported, not gated.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json). The public CLI batch excludes ten Sourcegraph public-snapshot inputs (Sourcegraph Enterprise license, which does not grant redistribution) and their two license notices, and sixteen TimescaleDB validation inputs from its source-available `tsl/` directory (Timescale License) and that license notice, leaving 89 CLI cases; every remaining input and expected output is unchanged. Native and held-out inputs are unchanged.

## Input distribution

- Public cases: 90.
- Held-out inputs: in repository.
- Input bodies stored in Git: 0 bytes.
- Input bodies acquired from authenticated upstreams before image build: 28,404,789 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task sqlfmt-cli
python3 tools/stage_payload.py --task sqlfmt-cli --fetch
python3 -m judge.public_input_preflight --task sqlfmt-cli --fetch
python3 -m tools.public_runtime_packet --task sqlfmt-cli \
  --cache /absolute/trusted-cache --out /absolute/new-sqlfmt-cli-agent-packet
```

The packet declaration defines the exact read-only runtime input roots.
Acquisition archives are not implicitly visible to the agent. An agent run
mounts the packet read-only; the source is then exported and scored separately. See
[public runtime packets](../../docs/public-runtime-packets.md).

On a hosted sandbox that cannot mount these inputs, run
`python3 tools/fetch_public_inputs.py --task sqlfmt-cli` from a temporary checkout
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
are under `tests/`. Held-out assets are authenticated at scoring.

Local reproduction, source-only exports and data terms are documented in
[tests/README.md](tests/README.md). All public SQL bodies are fetch-only.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.
