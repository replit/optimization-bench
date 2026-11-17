# Black verifier-only differential harness

The pristine native build is the oracle. This harness exercises only Black's
public Python formatting interfaces; it does not replace the protected
project CLI workload, timing steps or native build.

## Coverage

Seeded cases select original Python files from the image's frozen public
project trees (whole files up to 12 KB), append grammar snippets, add Unicode
comments or inject invalid indentation. Other cases cover empty strings,
malformed syntax, NUL and Unicode, fmt directives, functions, classes,
positional-only arguments, async comprehensions, pattern matching, type
parameters, a notebook cell magic and a bounded large literal collection.
No network or upstream test inputs are needed.

18 cases per seed sweep line lengths 40/88/120, preview and unstable
styles, string normalization, trailing commas, target versions Python
3.9–3.14, stub/notebook modes, skip-first-line, custom cell magics and every
individual Preview feature in the pinned release. Explicit line ranges and
both fast/safe `format_file_contents` calls are included.
This is bounded representative coverage, not a full Cartesian product.

Each case calls public `format_str` and `format_file_contents` with
independently constructed Mode objects in an A/B/A sequence. It then clears
one caller-owned target set, formats with that changed instance, and creates
a fresh independent Mode. These repeated parser/formatter calls expose
process-state and mutable-mode isolation errors without assuming thread
safety. Automatic target detection is exercised by the cleared target set.

Complete returned strings and complete exception type names/messages are
serialized. `NothingChanged` is retained as an exception outcome, not
discarded. The shared framework compares full runner output, stderr, exit
status and leftover count. No checksums, expected output tables, exception
filtering or candidate-specific paths are used.

## Validation and reproduction

The configured seeds are 1729, 2718 and 31415, with 18 cases each, a 30 s
per-case timeout and a 600 s total budget. Save current executed reports
outside the checkout. Historical image IDs and receipts do not qualify the
restored release.

The negative control changes only a scratch copy of
`src/black/linegen.py`: invert the condition
`if self.mode.string_normalization and leaf.type == token.STRING:`.
Rebuild the copy **natively** in the same image using the unchanged workload
build script and `BLACK_SOURCE=/control-source`. No pure-Python fallback or
compiled-module removal is used. The task's source tree remains unchanged.

Build `tasks/black-project-format/environment` as
`optimbench:black-project-format` and use the independently rebuilt reference
from the judge, not the preinstalled image baseline. After installing
`judge/requirements-interface.lock` in a
venv with `pip --require-hashes --only-binary=:all:`, run:

```sh
python -m judge.differential \
  --task tasks/black-project-format \
  --baseline /trusted/baseline --candidate /trusted/baseline \
  --image optimbench:black-project-format --cpuset 0-7 \
  --output /trusted/aa
```

Pass the native control build as `--candidate` and add `--seed 1729` to
exercise the negative control; expect `DifferentialMismatch`.
Replay the saved case through `judge.differential.execute` against both
builds and compare complete results with the saved `baseline` and
`candidate` records.

All differential cases run with DockerExecutor's `--network none`,
read-only builds, UID 10000, dropped capabilities and CPUs 0–7.
The public task image and control build use the unchanged ordinary
Dockerfile/build settings. No harness file is copied into the agent environment.
