# Goldmark native public-library probes

These verifier-only adapters exercise the selected build's exported parser,
renderer, and AST APIs. They are not benchmark drivers or agent-visible tools.
`rc_hooks.py` compiles the native Go probes against `build/project` in writable
scratch, using the pinned task image and offline module proxy.

`adapter.json` declares valid domains, complete observable effects, required
error fields, writer-fault coverage, and independent-AST aliasing. `adapter.py`
provides deterministic bounded generation, legal preconditions, shrinking, and
type-exact comparison. Unsupported faults are rejected, not silently ignored.

The native driver exercises healthy, short, and failing writers; fresh versus
persistent public calls; parser interleaving; caller mutation; and independent
AST metadata. Full rendered bytes, errors, legal caller-visible effects,
physical affinity, and post-call lifecycle observations are retained.
Diagnostics such as allocation counts are not contractual output.

Registration and limitations are in [rc-registration.md](rc-registration.md).
Run local adapter/registration regressions with:

```sh
python -m unittest discover -s tasks/goldmark-gfm-html/tests/fuzz -p 'test_*.py'
```

The four compressed files under `receipts/` are historical regression fixtures
used by `test_alias_receipts.py` and `test_lifecycle_receipts.py`; their assertions
do not establish current execution, current CPU availability, or qualification.
Unrelated historical campaign logs and optimizer outputs are not packaged.
