# Changelog

## 1.0.0 (2026-11-16)

First public release.

- Fourteen Tasks: Black, bzip2, Git, Goldmark, jq, NumPy, Ruff, serde_json,
  SQLFluff, sqlfmt, SQLGlot, SQLite, Terser and XZ.
- Prebuilt images for twelve Tasks on Public ECR
  (`public.ecr.aws/h1f5v5u6/optimbench:<task>-1.0.0`), pinned by digest;
  NumPy and SQLite are built locally from their `environment/` recipes.
- Reference scoring configuration: Intel `c4-standard-32`
  (Xeon Platinum 8581C), 8 dedicated judge CPUs, SMT off.
- Agent budget: 2 hours, 8 CPUs, 16 GiB, no network; the source left in the
  editable paths at the time limit is evaluated.
