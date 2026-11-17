# Serve ranked search and a ten-workload SQLite suite

Run eleven equally weighted timed native units using one candidate and image: ranked documentation search and ten suite batches running integrity_check, CREATE INDEX, JSON, printf, R*Tree, decimal, percentile, multi-way join, window-function and foreign-key-cascade workloads once per frozen request or document. Plant facets, authentic-record batch UPSERT and post-retention VACUUM are correctness-only cases, not timed units.

## Contract

Preserve SQLite C ABI, SQL/transaction/error/callback behavior and complete typed results. Each original request or genuine incoming record occurs once per unit. No added caches or process-global settings. Read units preserve complete request identities, ordered full records, exact ranks, lifecycle results and immutable inputs. Write units preserve every ordered field and type, schema, headers, settings, integrity, local notes, commit/close/shutdown success and source bytes. VACUUM must reclaim free pages and shrink the retained database. Suite workloads preserve complete typed results, per-request change counts and exported database images that pass an independent reader's integrity check and canonical row dump.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 14.
- Held-out inputs: in repository.
- Input bodies stored in Git: 1,393,158 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 203,266,180 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task sqlite-distinct-plant-facets
git lfs pull --include='tasks/sqlite-distinct-plant-facets/public/files/**' --exclude=''
python3 tools/stage_payload.py --task sqlite-distinct-plant-facets --fetch
docker build -t optimbench:sqlite-distinct-plant-facets tasks/sqlite-distinct-plant-facets/environment
docker run --rm --network none optimbench:sqlite-distinct-plant-facets /app/baseline-build/run
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

Public inputs stored with Git LFS total 4,436,488,096 bytes. They are kept in
`public/files/`, outside the agent context, and staging authenticates them
before builds. Held-out LFS bodies are not needed for public builds.

Each original request or record is executed once per native batch. Peak memory is reported but is not a correctness criterion.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
