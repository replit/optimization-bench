# Lint, repair and export complete SQL syntax

Run four separate equal-weight scored units in one image with one rebuilt SQLFluff candidate and optimization budget: original three-project lint, PostgreSQL auto-fix, Snowflake lint with Jinja, and complete BigQuery syntax-tree export.

## Contract

Byte-identical complete serialized results and preserved supported behavior; untouched upstream tests keep baseline outcomes; no input/evaluator detection, precomputation, delegation, persistent caches or processes outliving the operation.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 4.
- Held-out inputs: in repository.
- Input bodies stored in Git: 8,946 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 0 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task sqlfluff-project-lint
docker build -t optimbench:sqlfluff-project-lint tasks/sqlfluff-project-lint/environment
docker run --rm --network none optimbench:sqlfluff-project-lint /app/baseline-build/run
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

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
