# Verifier-only Goldmark differential harness

Run through `judge.differential.check`, not an alternative timing driver. The
protected directory is mounted read-only at `/verifier-differential`; execution
is network-disabled as the judge's unprivileged user, with a read-only selected
build and writable scratch only.

The seeded generator combines bounded slices from the frozen public Markdown
manifest with GFM tables, task lists, links/references, HTML, emphasis, fences,
nested lists/quotes, Unicode, controls, malformed syntax, invalid UTF-8, and
LF/CRLF/CR line endings. Missing real payloads fail without a substitute fixture.

The production driver runs an A/B/A document sequence three times in independent
processes. Complete output/error files, process status, stdout, and stderr are
retained as bytes, not summaries. A verifier-only Go helper additionally checks
32 combinations of GFM/core, safe/unsafe HTML, hard/normal wraps, HTML/XHTML,
and default/automatic heading IDs plus attributes. It exercises writer errors,
reused and independent parsers/contexts, mutation of returned AST nodes, and
rendering before and after an unrelated parse. No concurrency is invented.

The helper compiles against the selected build's `project` using the image's
pinned Go toolchain and offline module proxy. Compilation is correctness setup,
not benchmark timing; no helper enters an agent artifact.

The configured seeds and case/time budgets are in `../judge.json`. Run:

```sh
python -m judge.differential --task tasks/goldmark-gfm-html \
  --baseline <pristine-build> --candidate <candidate-build> \
  --image <immutable-task-image> --cpuset <available-cpus> \
  --output <fresh-directory>
```

A semantic negative control may change heading rendering in a scratch copy of
`renderer/html/html.go`, then rebuild through the protected production build
script. The shared comparator must reject the complete differing observation;
it must not edit expected outputs. A minimized reproducer can be replayed with
`judge.differential.execute` against both builds.

This bounded coverage is not exhaustive API correctness, a held-out corpus, or
a timing qualification. Run current checks; no historical result is asserted.
