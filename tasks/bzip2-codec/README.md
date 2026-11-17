# Compress and decompress natural files with bzip2

Compress and decompress complete natural files at block-size settings 1 and 9; decode complete text archives in small-memory mode.

## Contract

Byte-identical encoded stream and exact decoded original; retain CRCs, errors, full consumption and API behavior.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 253.
- Held-out inputs: in repository.
- Input bodies stored in Git: 0 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 0 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task bzip2-codec
git lfs pull --include='tasks/bzip2-codec/public/files/**' --exclude=''
python3 tools/stage_payload.py --task bzip2-codec --fetch
docker build -t optimbench:bzip2-codec tasks/bzip2-codec/environment
docker run --rm --network none optimbench:bzip2-codec /app/baseline-build/run
```

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out assets are available via Git LFS and are authenticated at scoring.

Public inputs stored with Git LFS total 30,606,044 bytes. They are kept in
`public/files/`, outside the agent context, and staging authenticates them
before builds. Held-out LFS bodies are not needed for public builds.

Each original public and held-out performance operation runs exactly once in one protected native process; the original individual operations and guards remain mandatory correctness-only cases. Native process startup, file I/O, allocation, CRCs, finalization and output flush are timed; input staging and authentication are not. Process startup may not exceed 2% of a unit. The small-text-batch unit decodes 75 public (65 held-out) complete block-9 text streams with small=1, grouped separately from the original batch and using the same codec build.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.
