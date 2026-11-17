# SQLGlot verifier-only differential harness

Enabled and validated in the task's unmodified Docker image. The pristine build
is the oracle; this is correctness coverage, not performance evidence.

## Coverage

`generate.py` reads the frozen, public Mozilla query bodies (bounded to 16 KB
per original), deterministically selects and recombines them with SQL grammar,
and adds comments, Unicode, empty/malformed queries, multiple statements,
CTEs, windows, joins, arrays, casts, JSON and a 256-column case. No corpus
download or database execution occurs in the harness.

24 cases per seed cover BigQuery→DuckDB, DuckDB→BigQuery, PostgreSQL→SQLite,
MySQL→PostgreSQL, Spark→Snowflake and TSQL→Oracle; all four parser ErrorLevel
settings; unsupported RAISE/IGNORE/WARN; and pretty, identify, normalize and
comment-generation options. This is a bounded representative matrix, not an
exhaustive Cartesian product of SQLGlot's dialects or generator settings.

The runner uses only public `transpile`, `parse_one`, Expression `copy`, `find`,
`set`, `transform`, `dump` and `sql` APIs. Each case executes A/B/A parser and
generator sequences with independent options and trees. Copy mutation and a
copying literal transform record the complete original before/after, changed
copy and independently reparsed tree, exposing AST aliasing/state leakage.

All returned strings, ordered lists and AST dumps are compared exactly.
Exceptions retain their full type name and message; all process stderr
(including parser/generator warnings), exit status and leftover count are
compared by `judge.differential`. There are no digest-only assertions,
expected answers, tolerance rules, error filtering or candidate detection.

## Executed validation

- Seeds: **1729, 2718, 31415**, 24 cases each, 72 matched cases and zero
  mismatches against an unchanged baseline.
- Full three-seed A/A runtime: **169.34 s**, within the configured 600 s
  total budget; per-case timeout is 30 s.
- Negative control: a scratch copy of `runtime/sqlglot/__init__.py` inserted
  `opts["pretty"] = False` immediately before `transpile` resolves its write
  dialect. Nothing in the task's source, inputs or build was changed.
  Seed 1729 caught this at case 0 in **11.58 s**, including shrinking.
- The framework shrank the SQL to `SELECT 1 AS a`. Two independent replays
  reproduced both complete saved outcomes exactly: **2.33 s** and **2.36 s**.
- `validation.json` contains the full A/A report, negative report, shrunk
  reproducer and exact-replay receipts. Generator determinism was checked
  with repeated `random.Random(seed)` runs.
- `tools/check_tasks.py` passed for all 21 task packages. Judge tests: 129 run
  (12 skipped); tools tests: 60 run (1 skipped), with no failures. Interface dependencies
  were installed from `judge/requirements-interface.lock` using
  `--require-hashes --only-binary=:all:`.

Validation image ID:
`sha256:8c1ab1d857d26e29d0f92e09b4d3b7b923727c8a941c6b98467047c7bb1413da`.
All differential execution used the shared DockerExecutor with `--network none`,
read-only builds, unprivileged UID, dropped capabilities and CPUs 0–7.

## Reproduce

From the repository root, install the locked interface requirements in a venv,
build `tasks/sqlglot-mozilla-transpile/environment` as
`optimbench:sqlglot-differential`, and export `/app/baseline-build` from that
image to a trusted host directory. Then run:

```sh
python -m judge.differential \
  --task tasks/sqlglot-mozilla-transpile \
  --baseline /trusted/baseline --candidate /trusted/baseline \
  --image optimbench:sqlglot-differential --cpuset 0-7 \
  --output /trusted/aa
```

For the negative control, copy the exported baseline to a separate trusted
directory, make only the documented semantic change, and pass that directory
as `--candidate` with `--seed 1729`. The command must raise
`DifferentialMismatch`. Replay the saved `reproducer.json` case through
`judge.differential.execute` against each build and compare the complete
results to its saved `baseline` and `candidate` fields.
