# Lint and format-check real Python projects with Ruff

Projects lint and format-check their original configured trees with Ruff in CI. Each project operation runs 14 cold Ruff processes on a fresh project copy: the original `ruff check`, `ruff check --select ALL --statistics` in stable and `--preview` modes for target versions py39 through py314, and `ruff format --check`, with eight Rayon threads and cache and fixes disabled.

## Contract

Byte-identical cli-output-v1 per project, unchanged supported CLI/API behavior and upstream baseline test outcomes. No input detection, hidden delegation, persistent caches, changed commands or arguments, or surviving helper processes.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json). Each of the four cases (pandas and FastAPI public; HTTPX and Rich held-out) runs its original `ruff check` plus a CI-style matrix (`ruff check --select ALL --statistics`, stable and `--preview`, target versions py39 through py314) and `ruff format --check`, so every timed unit lasts about a second or more. Projects, file inventories and the original command are unchanged; expected outputs were generated with the pristine Ruff build.

## Input distribution

- Public cases: 2.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 927,302 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 0 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task ruff-project-lint
docker build -t optimbench:ruff-project-lint tasks/ruff-project-lint/environment
docker run --rm --network none optimbench:ruff-project-lint /app/baseline-build/run
```

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out inputs are fetch-only; verification acquires and authenticates them outside the repository. Expected-result commitments remain authenticated by the held-out manifest.

The two public operations (pandas and FastAPI) keep their original projects and inventories. The held-out split uses separately pinned HTTPX and Rich projects with expectations from one untimed pristine authoring pass. Complete inputs, licenses, Git context and nonempty-file correspondence are bound in tests/heldout/edition.json. See [tests/README.md](tests/README.md).

Retain all original attribution in [notices/](notices/).
