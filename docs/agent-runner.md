# Running an agent and scoring its patch

An agent run has three steps:

1. **Run the agent in the task image for the 2-hour budget.** Start a container
   from the Task's image with the limits in its `task.toml` (8 CPUs, 16 GiB,
   `[agent] timeout_sec = 7200`). Give the agent the Task's `instruction.md`
   and nothing else from the repository: never mount the repository root,
   `tests/`, held-out data or the Docker socket into the agent container.
2. **Take the source left at the time limit.** When the budget ends, the
   source in the Task's editable paths, as it stands at the deadline, is what
   gets evaluated. Edits made after the deadline are not scored.
3. **Score it with the judge.** Export that source (only the editable
   roots declared in `release.json`'s `source_policy`) and run `judge.score` on
   an Intel x86-64 Linux machine matching the documented scoring configuration
   (8 dedicated judge CPUs, 16 GiB, SMT off). See
   [scoring-host-setup.md](scoring-host-setup.md).

Five images (jq, serde_json, sqlfmt, SQLGlot, Terser) contain no input data: mount the
public inputs read-only at `/app/workload/inputs/files` (see
[public-runtime-packets.md](public-runtime-packets.md)). On a platform that cannot mount
them, run this inside the container before step 1, deleting the checkout afterwards:

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone --quiet --depth 1 --branch optimbench-v1.0.0 \
  https://github.com/replit/optimization-bench.git /tmp/optimbench
python3 /tmp/optimbench/tools/fetch_public_inputs.py --task TASK
rm -rf /tmp/optimbench   # the agent must not see the repository or tests/
```

For step 3, copy the agent's source checkout as it stood at the deadline into a
directory, take an untouched copy of the same source from a fresh container of
the same image as the trusted pristine tree, and export the policy-admitted
edits:

```sh
python3 -m tools.export_candidate_source --task TASK_ID \
  --source /ABS/agent-source --pristine /ABS/trusted-pristine \
  --out /ABS/FRESH-source-export
python3 -m judge.score --task TASK_ID \
  --artifacts /ABS/FRESH-source-export/submission.json \
  --mode provisional --separate-sandbox \
  --image "$TASK_IMAGE" --out /ABS/FRESH-result
```

The [judge guide](judge.md) explains the result fields and what a clean result
requires. Run the agent and the judge on different machines (or at least never
score inside the agent's container or kernel).

## Optional helper scripts

`tools/agent_runner/` has helper scripts for a Linux machine that runs Claude
Code agents:

```sh
sudo bash tools/agent_runner/setup.sh          # Docker plugins and the pinned Claude Code binary
sudo bash tools/agent_runner/start_docker.sh &  # long-running; keep it up for the whole run
sudo bash tools/agent_runner/preflight.sh      # Docker, Buildx, Compose, 8-CPU cpuset, Claude Code pin
```

The pinned Claude Code is 2.1.310 at `/root/agent-install/claude` (SHA-256
`b73676430af12f97316a798b8371f81d640f90a58bdcb8f3ce56a835bb47df56`). Override
with `CLAUDE_VERSION`, `CLAUDE_SHA256` or `CLAUDE_BIN`.

`start_docker.sh` uses its own containerd root, because a stale default root can
hang on its bolt lock. Don't restart Docker while an image is building or an
agent is running: a restart can drop cached image layers and end the run before
the agent starts.

Clone with `GIT_LFS_SKIP_SMUDGE=1` and pull only the LFS objects a task needs.

## Legacy: Harbor

Earlier runs used the Harbor 0.24.0 harness. Harbor is no longer used. The
Harbor-era pieces that remain (`task.toml` fields such as `harbor_version`,
each Task's `tests/test.sh` calling `judge.harbor`, the Harbor artifact layout
accepted by `judge.score` and `tools/public_runtime_packet.py`'s generated
`harbor run` command) are kept for compatibility and history only; none is
needed to run or score an agent.
