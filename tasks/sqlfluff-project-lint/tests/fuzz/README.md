# SQLFluff correctness and native-process hooks

The verifier owns this directory. None of it is copied into the optimizing
agent image. It calls the SQLFluff runtime built from the submitted source;
it does not replace the library with a model or a toy implementation.

Run the focused structural/negative-control tests from the repository root:

```sh
PYTHONPATH=.:tools python3 -m unittest \
  tools.tests.test_sqlfluff_autofix tools.tests.test_sqlfluff_alternates
PYTHONPATH=.:tools python3 -m unittest discover \
  -s tasks/sqlfluff-project-lint/tests/fuzz -p 'test_*.py'
```

These unit tests are not a substitute for the actual protected image, native
worker and complete public/held-out execution described in
[the verifier guide](../README.md).

## Registered behavior

- `rc_hooks.py` supplies real stream-fault, persistent-repeat and aliasing
  callbacks for the shared classifier.
- `rc_api_cases.py`, `rc_api_worker.py` and `rc_api_facts.py` exercise public
  `Linter`, `FluffConfig`, parse, lint and file-persistence operations. They
  retain values, errors, complete streams, owned-file effects, GC/process state
  and observed kernel affinity in independent report files.
- `rc_driver.py` and `driver.py` exercise mutable `BlockConfig` ownership and
  legal frozen `MatchResult` reuse in fresh workers. Identity-only interning
  must not be rejected as a semantic change.
- `rc_cli.py` runs real small/large parse and fixed-suffix file-output commands
  under healthy, full-device, closed-reader, closed-stdout and read-only-output
  conditions. The shared controller owns fault injection and verdicts.
- `public-scope.json` binds the pinned pure-Python wheel and zero public
  C/C++ headers. This excludes only native header consumers; it does not waive
  applicable lifecycle, fault, API, CLI, CPU or candidate-coverage checks.

The driver report uses an inherited private FD, not candidate stdout, and
preserves the distinction between candidate faults, baseline nondeterminism
and missing evidence. Do not normalize away semantic errors or lifecycle
differences to force a pristine control through. Earlier registration and
campaign records are not qualification of the restored release.

The original fixed generator seeds and expected outcomes are retained.
Correctness-only generated cases use public grammar/API fixtures, not held-out
answers. Full fuzz/coverage or performance qualification is separate from
running these task preparation controls.

Only the two structural extraction/provenance artifacts needed by
`bind_surface.py` remain under `receipts/`. Historical campaigns, runtime image
overlays and operational readiness records are not distributed or treated as
evidence for this release.
