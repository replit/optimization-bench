# Compress source files and decode release archives with XZ Utils

Compress independent unchanged source files using one fresh preset-6 library call per file, and stream-decode complete upstream .tar.xz release archives with one fresh decoder per archive.

## Contract

Exact complete outputs, supported behavior, errors and all upstream tests.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 57.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 4,795,784 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 61,862,956 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task xz-lzma2-compress
docker build -t optimbench:xz-lzma2-compress tasks/xz-lzma2-compress/environment
docker run --rm --network none optimbench:xz-lzma2-compress /app/baseline-build/run
```

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out release archives are fetch-only; verification acquires and authenticates them outside the repository. The other held-out input bodies and all held-out expected outputs are stored with Git LFS and authenticated at scoring.

Two frozen performance units per scope: the original preset-6 compression case (public `batch`, held-out `source-files`) and `stream-decode`, which decodes complete single-block upstream .tar.xz release archives with lzma_stream_decoder(UINT64_MAX, LZMA_CONCATENATED) in one fresh process per archive. Decode results are the decoded byte count plus a protected XXH64 digest of every decoded byte. Correctness-only cases check CRC64 rejection and concatenated streams.

Retain all original attribution in [notices/](notices/).
