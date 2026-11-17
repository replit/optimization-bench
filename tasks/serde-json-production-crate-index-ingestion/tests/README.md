# Separate verifier

This directory is never copied into the agent image. `test.sh` is a compatibility
wrapper around the shared verifier; score with `judge.score`. There is no plumbing
fallback. Missing authenticated held-out
assets, upstream evidence, native observations or required host resources make
the run invalid rather than silently skipping those checks.

The frozen workloads contain two units in each split: `registry-ingest` and
`geojson-typed`. Every complete output is checked. Public input bodies are
authenticated anonymously in an external, manifest-bound BuildKit cache during
image construction. The original public-output checker reads that cache
read-only; neither input bodies nor temporary output bodies enter image layers.
BuildKit is required, and staged input bodies are excluded from the build
context. Held-out input bodies are fetch-only: `registry-ingest` files are
downloaded from the pinned crates.io index commit and `geojson-typed` files from
194 pinned geoBoundaries gbOpen release files into a temporary verifier view, and
are never stored in the repository, Git LFS or the agent context. Their sources
and licences are listed in `../notices/CRATES-IO-INDEX.md` and
`../notices/GEOJSON-SOURCES.md`. The six held-out `registry-ingest` expected
outputs (2,748,152 bytes) are not stored in Git or Git LFS either; for
`geojson-typed`, only each expected output's length and SHA-256 are stored.
Before scoring, regenerate them from the pinned inputs with the image's pristine
build using `tools/recover_serde_json_goldens.py`, as described in
[the recovery guide](../../../docs/serde-json-verifier-recovery.md).
`heldout.lock.json` binds every expected output's SHA-256 and every fetch-only
input's credential-free URL, byte length and SHA-256; recovery and staging fail
closed on any mismatch.

Exported images do not include the BuildKit input cache. Public execution uses
the shared authenticated external-input preparation path before admission, not
image-layer storage or workload-time downloads. An image by itself is not a
runtime input packet. Build with `--provenance=false`.

The protected `environment/public-input-packet.json` declares only
`inputs/files`, mounted read-only at `/app/workload/inputs/files` by the common
agent bridge. There is no task-specific derivation or preparation command.
Changing this declaration changes the protected image recipe and requires a
fresh canonical image binding.

`upstream.py` runs the pinned library's original tests under default and
supported feature profiles plus alloc-only checks. Test identities and outcomes
are compared between pristine and candidate. Differential generation and native
fault, repeat and ownership controls are documented in `differential/` and
`fuzz/`; they do not replace full workload correctness.

On a Linux/amd64 host with Docker, the locked host dependencies, at least eight
real visible CPUs and 16 GiB memory, build the task image and follow the common
candidate-export/scoring instructions in `docs/judge.md`. Correctness-only
held-out no-op and corruption controls use the same authenticated build path:

```sh
python3 -m judge.heldout_check \
  --task tasks/serde-json-production-crate-index-ingestion \
  --image optimbench:serde-json-production-crate-index-ingestion \
  --cpuset 0-7 --output /tmp/serde-heldout.json
python3 -m judge.heldout_check \
  --task tasks/serde-json-production-crate-index-ingestion \
  --image optimbench:serde-json-production-crate-index-ingestion \
  --cpuset 0-7 --negative-control --output /tmp/serde-heldout-negative.json
```

The negative control appends one byte to one observed output in verifier memory.
It does not edit a fixture, source, reference or candidate. It is successful only
when pristine originally matched and the altered output is rejected. These
commands perform no timing or optimization-model run. Host qualification and
performance claims remain governed by the unchanged shared scoring policy.
