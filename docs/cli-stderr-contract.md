# CLI diagnostic contract

`cli-stderr-contract-v1` compares stable error/panic headlines and classes,
not runtime implementation stack frames, goroutine identities or addresses.
It does not relax exit status, stdout, file contents or file side effects.
An unknown traceback format is retained conservatively instead of erased.

The authoritative implementation lives in the standalone protected runner so
it is available in agent and verifier images without an extra package:

```python
from judge.cli_stderr import stderr_contract, normalize_stderr, normalize_cli_observation
```

- `stderr_contract(bytes_or_text)` returns `version`, `class`, readable
  `headlines` and byte-exact `headlines_base64`. The latter prevents invalid
  UTF-8 bytes from colliding with a literal escaped string.
- `normalize_stderr` preserves the input type and ordinary diagnostics while
  collapsing recognized Go, Python, JS, Rust and Java runtime traces. Distinct
  panic/error messages remain distinct.
- `normalize_cli_observation` is for protected CLI record schemas only, never
  arbitrary application/library JSON. It preserves stdout/status/files and
  understands the existing plain/base64/hex stderr fields.

`run_case` normalizes step stderr after the executor clock ends and before the
protected collector. When bytes change, telemetry keeps the original base64,
its SHA-256 and the contract projection. No timing sample is altered.

Differential CLI generators opt in through protected
`differential.stderr_contract`. The comparison and shrink predicates use
normalized copies; raw observations and out-of-band preparation receipts remain
available for reproducers. Non-CLI library observations remain exact and are not
recursively reinterpreted as CLI metadata.

Audited CLI adapters: sqlfmt, Black, SQLFluff, jq and Git. Without this
contract their driver schemas would retain full stderr (sometimes with address
masking or sorted lines), exposing them to the same stack-coupling problem as
sqlfmt. Each task's instructions state the diagnostic policy explicitly.

The sqlfmt regression example retains the headline
`panic: runtime error: slice bounds out of range [140:139]` while ignoring
`formatStatements`/`runPaths` stack attribution. A different bound, panic class,
status, stdout byte or file side effect still fails comparison.
