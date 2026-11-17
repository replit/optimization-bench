# Render and parse GitHub-flavored Markdown with goldmark

Render 10,392 distinct complete public Markdown documents (116,306,743 bytes) to HTML once each, reusing one configured GFM parser/HTML renderer per unit, with exact per-document outputs. A second parse-only unit parses the same documents (heading IDs, GFM, footnotes) and walks the AST without rendering.

## Contract

The rendering unit configures one GFM parser/HTML renderer pair and reuses it across distinct whole documents, with fresh document context, AST and output buffers. The public unit has 10,392 documents (116,306,743 bytes); the family-disjoint held-out unit has 12,581 documents (122,436,684 bytes). Each input is processed exactly once, without repetition or padding. Superseded changelog revisions, normalized duplicates and documents without commit provenance are excluded; no source exceeds about half of a unit's bytes. The library, extensions and options, driver, declared output comparator and one-second minimum unit duration are fixed. The parse-only unit configures one parser with auto heading IDs, GFM and footnotes and emits per-document heading, link, image, autolink, fenced-code and footnote records from an AST walk over the same documents, without rendering; outputs are exact. See tests/README.md for public reproduction.

The workload, editable scope, prohibitions, source pins and expected results are
defined in [instruction.md](instruction.md), [task.json](task.json) and
[release.json](release.json).

## Input distribution

- Public cases: 10392.
- Held-out inputs: in repository.
- Input bodies stored in Git: 3,713,406 bytes.
- Input bodies reconstructed from authenticated upstreams at image build: 83,101,920 bytes.

Exact URLs, pinned hashes and sizes are in [payload.json](payload.json). All
downloads happen before compilation and execution, never during a measured workload.

## Build and run

From the repository root:

```sh
python3 tools/check_tasks.py --task goldmark-gfm-html
git lfs pull --include='tasks/goldmark-gfm-html/public/files/**' --exclude=''
python3 tools/stage_payload.py --task goldmark-gfm-html --fetch
docker build -t optimbench:goldmark-gfm-html tasks/goldmark-gfm-html/environment
docker run --rm --network none optimbench:goldmark-gfm-html /app/baseline-build/run
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

Public inputs stored with Git LFS total 29,491,417 bytes. They are kept in
`public/files/`, outside the agent context, and staging authenticates them
before builds. Held-out LFS bodies are not needed for public builds.

The original `gfm-html` and `parse-only` units, complete output contracts, and native verifier adapters are preserved. See [public reproduction](tests/README.md) for hydrated-export acquisition, source-only candidate submission, protected checks, licenses, and native generic gates.

Retain all original attribution in [notices/](notices/).

Held-out input licences and attributions are in [tests/notices/](tests/notices/).
