# Minify unchanged JavaScript assets

A build step sequentially minifies 149 independent, unchanged JavaScript assets in one Node process. A second build step minifies 7 unminified npm ES-module library dist files with webpack 5's production-default Terser options in one Node process.

## Contract

Preserve the pinned output for this workload, all supported library behavior and the complete existing upstream test suite.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json). The public cdnjs batch excludes the Highmaps 6.0.3 input (cdnjs label CC-BY-NC-3.0), leaving 149 assets; every remaining input and its expected output is unchanged. Held-out inputs are unchanged.

## Input distribution

- Public cases: 156.
- Held-out inputs: fetched at scoring.
- Input bodies stored in Git: 0 bytes.
- Input bodies acquired from authenticated upstreams before image build: 21,485,604 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task terser-minify
python3 tools/stage_payload.py --task terser-minify --fetch
recipe=$(python3 -c 'from judge.image_recipe import descriptor; print(descriptor("tasks/terser-minify")["recipe_sha256"])')
DOCKER_BUILDKIT=1 docker build --provenance=false \
  --label "org.optimbench.recipe-sha256=$recipe" \
  -t optimbench:terser-minify tasks/terser-minify/environment
packet="$PWD/tasks/terser-minify/environment/workload/inputs/files"
docker run --rm --network none \
  --mount "type=bind,source=$packet,target=/app/workload/inputs/files,readonly" \
  optimbench:terser-minify /app/baseline-build/run
```

These commands mount the authenticated public input files read-only at
`/app/workload/inputs/files`; the image does not contain them. An agent run
needs the same read-only mount.

On a hosted sandbox that cannot mount these inputs, run
`python3 tools/fetch_public_inputs.py --task terser-minify` from a temporary checkout
inside the container before the agent starts, then delete the checkout; see
[Running an agent](../../README.md#running-an-agent).

A prebuilt image is published to Public ECR and pinned by digest in
`task.toml`; you can also build it locally from `environment/`.

To run an agent, start it in this image with a 2-hour (7,200 s) limit. The source
left in the editable paths at the time limit is evaluated and then scored with
`judge.score`; see [agent-runner.md](../../docs/agent-runner.md).

## Verification

All execution uses the common [runner](../../docs/runner.md). The
[verifier](../../docs/judge.md) configuration and the original upstream test suite
are under `tests/`. Held-out inputs are fetch-only; verification acquires and authenticates them outside the repository. Expected-result commitments remain authenticated by the held-out manifest.

Two unchanged performance units: `cdnjs-assets` is the original cdnjs batch minus the non-commercially licensed Highmaps 6.0.3 input (149 assets); `webpack-esm` minifies seven pinned npm ES-module dist files with webpack 5's production-default Terser options. Each unit is one Node process. Every remaining public input and all held-out inputs keep their exact original output commitments. Public inputs are acquired before timing and mounted at runtime, not redistributed in the final image. Held-out inputs use verifier-only authenticated acquisition.

Retain all original attribution in [notices/](notices/).
