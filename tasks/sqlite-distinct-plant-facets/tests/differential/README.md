# Verifier-only SQLite differential harness

The shared executor uses a fresh container per case, seeds 1729/2718/31415,
twelve cases per seed, deterministic shrinking and mismatch reproducers.
`run.py` compiles `probe.c` against the selected build's own
`objects/libsqlite3.a` and generated `sqlite3.h`, linking that build's
`ext/misc/decimal.c` as the shell and the `dec` driver do. The probe runs each
case's SQL in order and records, per statement, column names, ordered rows
(integers as text, doubles as exact hexadecimal, text and blobs as bytes),
return and extended codes, `sqlite3_errmsg` after failures, changes and
autocommit; `--reopen` closes and reopens the database.

Cases cycle through DISTINCT/GROUP BY facets over a NOCASE column, FTS5
(porter, unicode61, prefix and trigram tokenizers; bm25, highlight, snippet,
optimize, integrity-check), UPSERT/RETURNING with savepoints and transaction
errors, foreign keys/partial and expression indexes/VACUUM/REINDEX with
integrity checks across a reopen, R*Tree including constraint failures, and
percentile/window/decimal/JSON/printf functions, with SQL error paths in each.
Every multi-row result has a total ORDER BY, and no case depends on scan order,
so a mismatch is a behavior difference rather than a plan choice.
