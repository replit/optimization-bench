# OptimizationBench

OptimizationBench measures whether an AI agent can make real open-source software
faster without changing what it does. Each Task pins a project, a workload
and an exact behavioral contract. An agent gets the Task's container image,
its `instruction.md` and two hours; a separate judge then rebuilds the
agent's source from scratch, checks every output against the original, and
times it against a freshly rebuilt pristine reference on a fixed reference
machine.

## Overview

- **Fourteen Tasks** across C, C++, Go, Rust, Python and JavaScript: bzip2,
  Black, Git, Goldmark, jq, NumPy, Ruff, serde_json, SQLFluff, sqlfmt,
  SQLGlot, SQLite, Terser and XZ.
- **One budget for every Task:** 2 hours, 8 CPUs, 16 GiB of memory and no
  network access.
- **Exact correctness:** outputs, errors, public interfaces and upstream
  tests must be preserved on public inputs and on held-out inputs of the same
  kind. A faster but incorrect candidate is rejected.
- **Conservative scoring:** the score is a lower confidence bound on the
  wall-clock speedup, measured with interleaved baseline/candidate trials and
  checked against an in-session A/A noise control. A change within the noise
  floor scores 1.0.

## Tasks

| Task | Project | What is timed |
| --- | --- | --- |
| [bzip2-codec](tasks/bzip2-codec) | bzip2 (C) | Compression and decompression of natural files |
| [black-project-format](tasks/black-project-format) | Black (Python) | Checking and reformatting real Python projects |
| [git-history-change-summary](tasks/git-history-change-summary) | Git (C) | History summaries with exact patches |
| [goldmark-gfm-html](tasks/goldmark-gfm-html) | Goldmark (Go) | GitHub-flavored Markdown rendering and parsing |
| [jq-strip-openapi-descriptions](tasks/jq-strip-openapi-descriptions) | jq (C) | Stripping OpenAPI descriptions, streaming GeoJSON and grouping GH Archive events |
| [numpy-quantile](tasks/numpy-quantile) | NumPy (C/Python) | Quantiles plus sort, search, set, numeric, time, random, text and I/O kernels on real data |
| [ruff-project-lint](tasks/ruff-project-lint) | Ruff (Rust) | CI-style linting and format checks of real Python projects |
| [serde-json-production-crate-index-ingestion](tasks/serde-json-production-crate-index-ingestion) | serde_json (Rust) | Parsing and re-serializing crate-index and GeoJSON data |
| [sqlfluff-project-lint](tasks/sqlfluff-project-lint) | SQLFluff (Python) | SQL linting, repair and syntax export |
| [sqlfmt-cli](tasks/sqlfmt-cli) | sqlfmt (Go) | SQL formatting through the CLI and the native API |
| [sqlglot-mozilla-transpile](tasks/sqlglot-mozilla-transpile) | SQLGlot (Python) | SQL transpilation, optimization, lineage and diff |
| [sqlite-distinct-plant-facets](tasks/sqlite-distinct-plant-facets) | SQLite (C) | Ranked search and a suite of native SQL workloads |
| [terser-minify](tasks/terser-minify) | Terser (JavaScript) | Minifying unchanged JavaScript assets and ES-module libraries |
| [xz-lzma2-compress](tasks/xz-lzma2-compress) | XZ Utils (C) | LZMA2 compression and `.tar.xz` stream decoding |

Each Task directory contains the agent's `instruction.md`, a README with
its workload, inputs and data terms, the image recipe under `environment/`,
and the verifier under `tests/`. The catalog below is generated from the
Task definitions by `tools/update_catalog.py`.

<!-- task-table:start -->
| Task | Public cases | Git input bodies | Upstream input bodies | Held-out inputs |
| --- | ---: | ---: | ---: | --- |
| [black-project-format](tasks/black-project-format) | 5 | 2.44 MB | 0 B | in repository |
| [bzip2-codec](tasks/bzip2-codec) | 253 | 0 B | 0 B | in repository |
| [git-history-change-summary](tasks/git-history-change-summary) | 7 | 2.07 MB | 29153.40 MB | fetched at scoring |
| [goldmark-gfm-html](tasks/goldmark-gfm-html) | 10392 | 3.71 MB | 83.10 MB | in repository |
| [jq-strip-openapi-descriptions](tasks/jq-strip-openapi-descriptions) | 33 | 4.53 MB | 103.15 MB | fetched at scoring |
| [numpy-quantile](tasks/numpy-quantile) | 28 | 0 B | 0 B | in repository |
| [ruff-project-lint](tasks/ruff-project-lint) | 2 | 927,302 B | 0 B | fetched at scoring |
| [serde-json-production-crate-index-ingestion](tasks/serde-json-production-crate-index-ingestion) | 21206 | 0 B | 1810.17 MB | fetched at scoring |
| [sqlfluff-project-lint](tasks/sqlfluff-project-lint) | 4 | 8,946 B | 0 B | in repository |
| [sqlfmt-cli](tasks/sqlfmt-cli) | 90 | 0 B | 28.40 MB | in repository |
| [sqlglot-mozilla-transpile](tasks/sqlglot-mozilla-transpile) | 633 | 912,983 B | 532,813 B | in repository |
| [sqlite-distinct-plant-facets](tasks/sqlite-distinct-plant-facets) | 14 | 1.39 MB | 203.27 MB | in repository |
| [terser-minify](tasks/terser-minify) | 156 | 0 B | 21.49 MB | fetched at scoring |
| [xz-lzma2-compress](tasks/xz-lzma2-compress) | 57 | 4.80 MB | 61.86 MB | fetched at scoring |
<!-- task-table:end -->

"Fetched at scoring" means the held-out inputs are not stored in Git: the verifier downloads them from their pinned upstream sources and checks every file's SHA-256 before use. Upstream input bodies are fetched the same way. serde_json's held-out expected outputs are not redistributed; they are rebuilt locally and verified byte for byte.

## Setup

Requirements: Linux x86-64, Docker with BuildKit, Git LFS, and CPython 3.12
or 3.14 for the host tools.

Task inputs are stored with Git LFS (about 16 GB in total, most of it for
NumPy and SQLite). Clone without downloading them, then fetch only the Tasks
you need:

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/replit/optimization-bench.git
cd optimization-bench
git lfs install --local --skip-smudge
git lfs pull --include "tasks/TASK_ID/**"
```

Then set up the host tools:

```sh
python3 -m venv .venv-host
.venv-host/bin/python -m pip install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
export PATH="$PWD/.venv-host/bin:$PATH"
export PYTHONPATH=.:tools

python3 -m tools.release_preflight --require-generic-hooks
python3 -m unittest discover -s tools/tests
python3 -m unittest discover -s judge/tests
```

## Task images

All fourteen Tasks have a prebuilt image on Public ECR, pinned by digest in each
Task's `task.toml`:

```sh
docker pull public.ecr.aws/h1f5v5u6/optimbench:sqlglot-mozilla-transpile-1.0.0
```

Every image can also be rebuilt from its `environment/` recipe. Each Task's
README gives its exact build and check commands; NumPy and SQLite, for
example, stage their inputs first:

```sh
task=numpy-quantile   # or sqlite-distinct-plant-facets
python3 tools/stage_payload.py --task "$task" --fetch
docker build -t "optimbench:$task" "tasks/$task/environment"
```

## Running an agent

1. Start the Task's image with 8 CPUs, 16 GiB and no network, and give the
   agent only the Task's `instruction.md`. Do not mount the repository,
   `tests/`, held-out data or the Docker socket into the agent's container.
2. Stop the container at the agent time limit (2 hours by default; see
   [Prompt variables](#prompt-variables)). **The source left in the editable
   paths at that moment is the submission**, whether or not it was committed.
3. Copy the editable source out of the stopped container and score it as
   below.

**Hosted sandboxes that cannot mount inputs.** The jq, serde_json, sqlfmt,
SQLGlot and Terser images contain no input data; their public inputs are normally
mounted read-only at `/app/workload/inputs/files`. If your platform starts the
container from the image alone, run this inside it once before the agent starts
(replace `TASK`). It downloads each input from its pinned original source and checks
every file's SHA-256:

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --depth 1 --branch optimbench-v1.0.0 \
  https://github.com/replit/optimization-bench.git /tmp/optimbench
python3 /tmp/optimbench/tools/fetch_public_inputs.py --task TASK
rm -rf /tmp/optimbench   # the agent must not see the repository or tests/
```

[docs/agent-runner.md](docs/agent-runner.md) describes the procedure and the
optional helper scripts.

### Prompt variables

Each Task's `instruction.md` is rendered from `instruction.md.njk`, a template
with `{{ ... }}` placeholders filled from the Task's `task.toml`. The committed
`instruction.md` is always the default rendering, which is what official runs
use. To run with other values, re-render your local copy; for example, a
30-minute agent time limit:

```sh
python3 -m tools.render_instruction --all --set agent_timeout=30m
python3 -m tools.render_instruction --all          # restore the defaults
```

`--task TASK_ID` renders a single Task and `--stdout` prints instead of writing.
Available variables: `agent_timeout` (default 7,200 seconds; accepts `600`,
`90m` or `2h`). Templates are Jinja/Nunjucks-compatible, so other harnesses can
render them directly. Report any non-default values alongside results.

## Scoring

Score on the reference scoring machine, never inside the agent's container:
Google Cloud `c4-standard-32` (Intel Xeon Platinum 8581C), one thread per
core, with CPUs 0-7 for the judge and CPUs 8-15 for the system. Any other
machine runs the full evaluation but reports `claimable: false`.

1. **Prepare the machine once** (as root, from a checkout cloned as in
   [scoring-host-setup.md](docs/scoring-host-setup.md)). This installs Docker
   and the host tools, quiets background services, reserves CPUs 8-15 for the
   system and creates the judge's virtual environment:

   ```sh
   sudo bash tools/provision_scoring_host.sh
   sudo JUDGE_VENV=/opt/judge sh tools/setup_scoring_host.sh
   ```

   Provisioning also lowers Docker's bridge MTU and clamps TCP MSS, which
   cloud networks require. That repair is lost when the machine reboots or
   Docker restarts; rerun it before scoring again:

   ```sh
   sudo python3 -m tools.scoring_host fix-network --mtu 1400 --confirm-no-timing-session
   ```

2. **Take a trusted pristine copy** of the Task's project directory from a
   fresh container of the same image. The project directory is `/app/<project>`,
   the top directory of the `artifacts` paths in the Task's `task.toml`:

   ```sh
   cid=$(sudo docker create "$TASK_IMAGE")
   sudo docker cp "$cid:/app/PROJECT" /ABS/trusted-pristine
   sudo docker rm "$cid"
   ```

3. **Run the control once per boot.** Score the unchanged source on the same
   machine before any candidate. It must come back correct and inconclusive;
   the judge uses it to confirm the machine is fit to time on. The control's
   own result reports `claimable: false` (`qualification_missing`); that is
   expected:

   ```sh
   J="sudo taskset -c 0-7 /opt/judge/bin/python"
   $J -m tools.export_candidate_source --task TASK_ID \
     --source /ABS/trusted-pristine --pristine /ABS/trusted-pristine \
     --out /ABS/control-export
   $J -m judge.score --task TASK_ID \
     --artifacts /ABS/control-export/submission.json \
     --mode provisional --separate-sandbox --stop-on-gate --cpuset 0-7 \
     --image "$TASK_IMAGE" --out /ABS/control
   ```

4. **Score the agent's source.** Export only the Task's declared editable
   roots from the agent's copy, then score it against the control:

   ```sh
   $J -m tools.export_candidate_source --task TASK_ID \
     --source /ABS/agent-source --pristine /ABS/trusted-pristine \
     --out /ABS/candidate-export
   $J -m judge.score --task TASK_ID \
     --artifacts /ABS/candidate-export/submission.json \
     --mode provisional --separate-sandbox --stop-on-gate --cpuset 0-7 \
     --image "$TASK_IMAGE" --session-control /ABS/control/result.json \
     --out /ABS/result
   ```

Run every command from the repository root. Each `--out` directory must be
new. A control stays valid until the machine reboots or the checkout changes.
For a claimable result the checkout must also be clean and carry a release tag
(`optimbench-v1.0.0`).

The judge rebuilds the candidate, runs the upstream tests and every public
and held-out correctness check, then samples baseline/candidate timings
alongside an A/A control. `result.json` reports the status, the speedup and
its confidence interval, whether the result is claimable, and any gate or
review flags. An inconclusive result, or a score of 1.0, is not a
demonstrated speedup. The [judge guide](docs/judge.md) documents every field.

Before scoring serde_json, regenerate its held-out expected outputs, which
are not redistributed, from the published image (run under `taskset -c 0-7`):

```sh
python3 tools/recover_serde_json_goldens.py --image "$TASK_IMAGE" --cpuset 0-7 \
  --fetch --cache /ABS/serde-cache --output /ABS/serde-originals
python3 tools/stage_payload.py --task serde-json-production-crate-index-ingestion \
  --scope heldout --from /ABS/serde-originals
```

## Data and licensing of inputs

Task inputs come from public projects and datasets, each under its own
terms, and remain under their original licenses and copyright.

- **Shipped where permitted.** Inputs are stored in this repository or the
  images only where their terms permit redistribution, together with their
  license texts and attribution.
- **Everything else is downloaded by you.** Other inputs are listed as pinned
  URLs with SHA-256 hashes, and the documented tools fetch them from the
  original publisher. Downloading them means accepting that publisher's terms.
- **Provenance for every input.** Each Task's `payload.json`, `provenance.json`
  and notices record where every input came from, its license and what was
  changed, so you can follow those licenses when you use or redistribute it.
- **Held-out inputs** are public but kept out of the agent's image.

The inputs are provided for benchmarking and research, without warranty. The
policy, copyleft obligations and removal requests are covered in
[docs/public-data-policy.md](docs/public-data-policy.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `tasks/` | One directory per Task: instruction, image recipe, verifier, provenance |
| `judge/` | The judge: source admission, rebuilds, comparators, sampling and statistics |
| `tools/` | Payload staging, input rebuilding, source export and repository checks |
| `datasets/` | The v1.0 Task selection, scoring policy and data-release ledger |
| `docs/` | Task format, judge, scoring-host and statistics documentation |

## Contributing

New Tasks follow [docs/task-format.md](docs/task-format.md) and the
checklist in [docs/adding-a-task.md](docs/adding-a-task.md). Issues and pull
requests are welcome.

## Security

Please report sandbox escapes, ways to influence a score without a genuine
speedup, or exposed data privately, as described in [SECURITY.md](SECURITY.md).

## Citation

```bibtex
@software{optimizationbench2026,
  title   = {OptimizationBench: Can AI Agents Make Real Software Faster Without Breaking It?},
  author  = {Vaibhav Kumar and Ibrahim Sheikh and Peter Zhong and Saurav Joshi and Patrick Ma and Robin Newhouse and Akshay Budhkar and Michele Catasta},
  year    = {2026},
  version = {1.0.0}
}
```

## License

The code and documentation in this repository are licensed under the
[Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Third-party source code,
datasets and inputs used by the Tasks remain under their original licenses,
listed in each Task's notices.
