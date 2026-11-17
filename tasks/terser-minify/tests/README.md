# Separate verifier and public reproduction

The agent receives `environment/`, never this directory or the shared judge.
The public manifest contains 149 cdnjs assets (Highmaps 6.0.3 removed on 2026-11-15) and seven webpack ES-module
assets. The separate frozen held-out manifest contains 149 and eleven respectively.
Apart from that removal, both manifests, every source byte commitment, every expected length/hash, both
timing boundaries and all sampling settings are unchanged from the pinned task.

## Acquire inputs before execution

Run from the repository root after the locked host dependencies in `docs/judge.md`
are installed:

```sh
.venv-host/bin/python -m judge.public_input_preflight --task terser-minify --fetch
DOCKER_BUILDKIT=1 docker build -t optimbench:terser-minify tasks/terser-minify/environment
docker run --rm --network none --cpuset-cpus 0-7 --memory 16g \
  --mount "type=bind,src=$PWD/tasks/terser-minify/environment/workload/inputs,dst=/app/workload/inputs,readonly" \
  optimbench:terser-minify /app/baseline-build/run
```

Select eight CPUs actually available on the host instead of assuming `0-7`.
The final image contains the protected library, build and drivers, but no public
input bodies. Its full-output build guard uses a transient read-only build mount;
the original assets do not enter final image layers. Runtime acquisition and
authentication finish before the timed Node process. Do not start an agent or
public replay without the authenticated public mount.

All 160 held-out sources have exact-size/SHA-256-pinned, credential-free HTTPS
references in `heldout.lock.json`. The existing shared judge acquires them outside
the checkout and rejects retained fetch-only bodies in the checkout. No Git LFS
download or credentials are required. `fuzz/heldout-sources.json` records the
authenticated publisher copies; the npm members have their verbatim MIT licenses in
`fuzz/HELDOUT-LICENSES.md`. Each held-out row in `../payload.json` records an SPDX
license expression (`license`) and the evidence for it (`license_note`).

## Source-artifact control and held-out verification

This creates an unmodified source control, not an optimization candidate:

```sh
mkdir -p /tmp/terser-export
container=$(docker create optimbench:terser-minify)
docker cp "$container:/app/terser/lib" /tmp/terser-export/lib
docker rm "$container"
printf '{"lib":"/tmp/terser-export/lib"}\n' >/tmp/terser-artifacts.json
.venv-host/bin/python -m judge.score --task terser-minify \
  --artifacts /tmp/terser-artifacts.json --image optimbench:terser-minify \
  --gates-only --separate-sandbox --cpuset 0-7 \
  --cache /tmp/terser-reference-cache --out /tmp/terser-public-gates
.venv-host/bin/python -m judge.heldout_check \
  --task tasks/terser-minify --artifacts /tmp/terser-artifacts.json \
  --image optimbench:terser-minify --cpuset 0-7 \
  --cache /tmp/terser-reference-cache --output /tmp/terser-heldout.json
```

The shared judge authenticates public inputs and materializes them in its
image-runtime cache, including for input-free images. `tools/replay_tasks.py`
uses the same preparation before both parity arms, including with `--skip-build`.
Image construction alone does not populate that runtime cache. This wiring does
not establish anonymous end-to-end bootstrap: source, toolchain and image
acquisition from fresh public release URLs remains unverified.

Use fresh output/export directories and an isolated verifier host with no competing
measurement jobs. The isolation flag is an operator attestation, not evidence by
itself. The source export is rebuilt by the common protected build pipeline.
The gates-only command performs no sampling and verifies public scope; the
separate held-out check must also pass before claiming full correctness.

Golden *bodies* are not redistributed: they include third-party code whose
copyright comments the original minification workload removes. The original
complete-output lengths and SHA-256 commitments remain in the authenticated
manifest. The common judge first rebuilds the pinned pristine library, requires
its complete outputs to match those commitments, and only then compares the
candidate. Never update a commitment to accommodate an output difference.

`test.sh` (a thin entry point; score with `judge.score`) and `Dockerfile` remain the separate verifier.
Registered host qualification and performance claims are not
established merely by these commands. Review input usage restrictions in
`../notices/INPUT-DISTRIBUTION.md` before using or distributing derived artifacts.
