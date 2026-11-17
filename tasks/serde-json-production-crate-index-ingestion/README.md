# Ingest Rust registry metadata and typed GeoJSON with serde_json

Two equally weighted timed units, each one long-lived process: a registry metadata service ingests 20,999 distinct complete crate histories through the crates-index typed parser and serializes every full typed result, and a GeoJSON service deserializes 207 complete FeatureCollections into a typed RFC 7946 schema through serde_json. Every complete result is materialized and compared.

## Contract

Preserve full public serde_json APIs/features/errors/ownership and complete typed output. No new global state tuning unrelated to allowed parallelism,caches,dependency replacement or input/schema/config changes. Upstream identities/outcomes must be unchanged.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 21206.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 0 bytes.
- Input bodies acquired from authenticated upstreams before image build: 1,810,169,363 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task serde-json-production-crate-index-ingestion
python3 tools/stage_payload.py --task serde-json-production-crate-index-ingestion --fetch
python3 -m judge.public_input_preflight --task serde-json-production-crate-index-ingestion --fetch
python3 -m tools.public_runtime_packet --task serde-json-production-crate-index-ingestion \
  --cache /absolute/trusted-cache --out /absolute/new-serde-json-production-crate-index-ingestion-agent-packet
```

The packet declaration defines the exact read-only runtime input roots.
Acquisition archives are not implicitly visible to the agent. An agent run
mounts the packet read-only; the source is then exported and scored separately. See
[public runtime packets](../../docs/public-runtime-packets.md).

On a hosted sandbox that cannot mount these inputs, run
`python3 tools/fetch_public_inputs.py --task serde-json-production-crate-index-ingestion` from a temporary checkout
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
are under `tests/`. Held-out inputs are fetch-only; verification downloads them from the pinned crates.io index (`registry-ingest`) and from 194 pinned geoBoundaries gbOpen GeoJSON files (`geojson-typed`), and authenticates them outside the repository. The six held-out `registry-ingest` expected outputs are not stored in Git or Git LFS: regenerate them with `tools/recover_serde_json_goldens.py` before scoring (see [the recovery guide](../../docs/serde-json-verifier-recovery.md)); they must match the SHA-256 commitments in `tests/heldout.lock.json`. crates.io index terms are summarized in [notices/CRATES-IO-INDEX.md](notices/CRATES-IO-INDEX.md); GeoJSON sources, licences and attribution for both splits are in [notices/GEOJSON-SOURCES.md](notices/GEOJSON-SOURCES.md).

Retain all original attribution in [notices/](notices/).
