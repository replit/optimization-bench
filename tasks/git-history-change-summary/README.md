# Summarize Git history with exact patches

Seven equally weighted units run normal Git command-line work over distinct original repositories. Three units run `git diff-tree --stdin` over 32,768 commits from Git, Cargo, Django and Prettier: full Myers patches for 16,384 commits, and Myers shortstats and histogram patches for 8,192 other commits each. Three more produce complete patches for distinct commits of other repositories with `--ignore-all-space`, the patience algorithm and `-U32767` context. The seventh runs `git merge-file -p` on base, ours and theirs blobs from real merge commits.

## Contract

Preserve xdiff public declarations, structs, callbacks, exact patch/stat output and upstream test identities/outcomes. No test/fixture/snapshot changes, precomputed outputs or new process-global state.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 7.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 2,071,929 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 29,153,399,621 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task git-history-change-summary
docker build -t optimbench:git-history-change-summary tasks/git-history-change-summary/environment
docker run --rm --network none optimbench:git-history-change-summary /app/baseline-build/run
```

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out inputs are fetch-only; verification acquires and authenticates them outside the repository. Expected outputs are stored with Git LFS.

Input licences, attributions and changes made are in
[environment/notices/](environment/notices/); retain them when redistributing.

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
