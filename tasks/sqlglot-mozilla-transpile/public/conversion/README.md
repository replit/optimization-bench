# Conversion unit: held-out expansion

The conversion unit transpiles Mozilla bigquery-etl SQL from BigQuery to DuckDB
with `sqlglot.transpile` (`read="bigquery"`, `write="duckdb"`, `pretty=False`,
error and unsupported levels `RAISE`). Every file is converted once per run.

- Public: 284 `query.sql` files (`../dev/`).
- Held-out: 327 files: 122 `query.sql` files and 205 `view.sql` files (including
  `CREATE VIEW` definitions). They ship under `tests/heldout/` (bodies in Git LFS),
  outside the agent image.

All conversion inputs come unmodified from Mozilla bigquery-etl (MPL-2.0) at
commit `1819e636d900f935b9b464b19b00d05aa207d210` (`source.lock.json`). Licence
and attribution details are in `environment/notices/INPUTS.md` and
`tests/notices/HELDOUT.md`.

## How the 205 view files were selected

The 205 `view.sql` files extend the original 122 held-out `query.sql` files. They
were selected from the 691 view files at the same commit by excluding files that
repeat raw bytes, normalized statement shapes or templates already present in the
public or held-out sets, and files that produce warnings or use unsupported
operations. When both a view definition and its underlying query are present,
both are compared; the first source path in sorted order represents a repeated
shape. No selection by timing, padding or repetition was used, and no source file
was edited. Per-file source paths and hashes are in
`tests/heldout/conversion-expansion-provenance.json`.

## Tools

- `prepare_private.py` reproduces and verifies the expansion from the pinned
  archive (`--runtime`, `--archive`, `--diagnostics`). Without `--freeze` it only
  verifies the committed files. It authenticates the original held-out files,
  manifests and payload commitments first and never replaces a committed file.
  Run it in the task image with `PYTHONHASHSEED=0 python -P prepare_private.py`.
- `test_authoring.py` contains synthetic regression tests for that tool.
- `test_workload.py` checks complete outputs for the public and held-out
  conversion and optimizer cases, and rebuilds wrong-result, skipped-work and
  slowed-but-correct controls: wrong, skipped and missing results must fail;
  slowed correct results must still match.
- `measurements.json` records development measurements (cProfile Python self
  time); it is not a scoring input.
