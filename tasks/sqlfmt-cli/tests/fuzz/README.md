# Protected sqlfmt contracts

The hooks are registered by the shared generic judge; metadata registration
alone is not execution evidence. The required executable checks cover:

- Native API shape, returned strings/errors, immutability and independent calls.
- All original CLI argument, stream, close/error, repeat and output-file effects.
- Process-global runtime state, imported public interfaces and upstream outcomes.
- Correctness on the protected 1/2/4/8-CPU matrix.
- Both public workload units, complete output bytes and post-run cleanup.

`hooks.py` contains the protected expectations; `native/contract.go` is compiled
against the candidate's submitted library. `test_*.py` exercises verifier
fixtures and negative expectations. The real `native_validate.py` controls
rebuild unchanged, wrong, omitted-output and slower candidates from source;
they are not mock verdicts. Historical receipts are not evidence for this
release and are not distributed.

Run the exact public and held-out paths in [the reproduction guide](../README.md).
The native corpus's source and reference commitments are documented
[separately](native/README.md). `public_cli_recipes.py` can rederive acquisition
metadata from already authenticated frozen input bodies; it never authors
expected outputs or changes a timed case.
