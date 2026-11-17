# Strip OpenAPI descriptions, stream GeoJSON and process GH Archive events

Four equally weighted performance units share one jq build and image: the original native OpenAPI batch, GeoJSON feature streaming, complete-hour event grouping/counting/sorting, and complete-hour event-to-CSV conversion. The native unit contains 30 public documents; the three CLI units each consume one complete input. Downloads, gzip decoding and integrity checks occur before timing.

The image contains code and frozen metadata, not input bodies. Build guards read an authenticated public packet through transient read-only mounts; runtime setup supplies those same files read-only at `/app/workload/inputs/files` before execution. A bare image run without that packet fails closed. For setup, source export and separate public and held-out evaluation, follow the [public runtime packet guide](../../docs/public-runtime-packets.md) and the [judge guide](../../docs/judge.md). GH Archive event bodies and their derived outputs are fetch-only and not redistributed: the public and held-out hours are each downloaded from GH Archive at a pinned URL and SHA-256, outside the checkout.

## Contract

Preserve original OpenAPI output bytes and supported jq behavior. All three CLI units compare complete stdout, stderr and exit status. Held-out GH Archive inputs are fetched outside Git. GH Archive event bodies are fetch-only and are not redistributed: no GH Archive event body or derived output body is committed.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 33.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 4,525,235 bytes.
- Input bodies acquired from authenticated upstreams before image build: 103,154,320 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task jq-strip-openapi-descriptions
git lfs pull --include='tasks/jq-strip-openapi-descriptions/public/files/**' --exclude=''
python3 tools/stage_payload.py --task jq-strip-openapi-descriptions --fetch
python3 -m judge.public_input_preflight --task jq-strip-openapi-descriptions --fetch
python3 -m tools.public_runtime_packet --task jq-strip-openapi-descriptions \
  --cache /absolute/trusted-cache --out /absolute/new-jq-strip-openapi-descriptions-agent-packet
```

The packet declaration defines the exact read-only runtime input roots.
Acquisition archives are not implicitly visible to the agent. An agent run
mounts the packet read-only; the source is then exported and scored separately. See
[public runtime packets](../../docs/public-runtime-packets.md).

On a hosted sandbox that cannot mount these inputs, run
`python3 tools/fetch_public_inputs.py --task jq-strip-openapi-descriptions` from a temporary checkout
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
are under `tests/`. Held-out data is partly stored and partly fetched. Nine OpenAPI specifications (four MIT, five Apache-2.0; their licence files are in `tests/heldout/references/`), one City of Toronto GeoJSON file (Open Government Licence – Toronto) and their ten expected outputs are stored with Git LFS under `tests/`, outside the agent image. The held-out GH Archive hour is fetch-only: verification downloads it from its pinned URL and checks its SHA-256 outside the repository, and only length and SHA-256 commitments of its outputs are stored.

Public inputs stored with Git LFS total 12,396,533 bytes. They are kept in
`public/files/`, outside the agent context, and staging authenticates them
before builds. Held-out LFS bodies are not needed for public builds.

Retain all original attribution in [notices/](notices/).
