# Compute quantiles and related NumPy operations over real data

Compute linear quantiles over resident arrays, plus six timed numeric, time, random and I/O units over real data. Two further batches (strings-text, held-out only, and genfromtxt-catalog) are correctness-only.

## Contract

Quantile results: exact shape/dtype; rtol=atol=8*eps(float64), equal NaNs. Suite units: exact canonical results, except interp-equalize (absolute 8*eps(float64)) and loadtxt-csv (equal NaNs). Preserve input buffers.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 28.
- Held-out inputs: in repository.
- Input bodies stored in Git: 0 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 0 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task numpy-quantile
git lfs pull --include='tasks/numpy-quantile/public/files/**' --exclude=''
python3 tools/stage_payload.py --task numpy-quantile --fetch
docker build -t optimbench:numpy-quantile tasks/numpy-quantile/environment
docker run --rm --network none optimbench:numpy-quantile /app/baseline-build/run
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

Public inputs stored with Git LFS total 1,323,952,201 bytes. They are kept in
`public/files/`, outside the agent context, and staging authenticates them
before builds. Held-out LFS bodies are not needed for public builds.

Seven equally weighted timed units per scope: the linear-quantile batch and six suite units. The strings-text (held-out only) and genfromtxt-catalog batches are correctness-only checks.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
