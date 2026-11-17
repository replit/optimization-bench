# Check and reformat real Python projects

Projects check formatting in CI and apply formatting changes during style migrations. The four timed units are django-reformat, sqlalchemy-check, warm-snippets and editor-ranges. Each project case is one cold `python -m black` process on a fresh original project tree with eight workers. The warm-process API units format documentation code fragments with black.format_str and serve editor range-formatting requests with black.format_file_contents(..., lines=...). Each selected input is visited once per operation without repetition or padding. startup-empty is a separate correctness-only probe on an empty project.

## Contract

Byte-identical black-output-v1 result per operation; upstream tests keep the baseline's results (native build, plus the mypyc-incompatible tests as pure Python); native build loaded; no helper processes outliving the operation, no caches or files outside declared outputs, no input or evaluator detection, no delegation.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 5.
- Held-out inputs: in repository.
- Input bodies stored in Git: 2,439,007 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 0 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task black-project-format
docker build -t optimbench:black-project-format tasks/black-project-format/environment
docker run --rm --network none optimbench:black-project-format /app/baseline-build/run
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

There are four equally weighted public timed units: django-reformat, sqlalchemy-check, warm-snippets and editor-ranges. startup-empty is a separate correctness-only probe, never a fifth timed unit. The held-out split has four corresponding units. See [tests/README.md](tests/README.md) for clean-checkout acquisition, candidate export and self-hosted judge commands.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.
