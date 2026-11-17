# Make SQLFluff faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
Four separate, equal-weight scored units use the same rebuilt candidate and image. None may be omitted:

1. **Original project lint:** three ordered cold stages on fresh copies: pgledger, 126 distinct Almanac SQL files, and five Jaffle Shop Classic models. Preserve configuration, ignores, Jinja builtins and eight workers. No database or dbt adapter is used.
2. **PostgreSQL auto-fix:** repair each declared original SQL file once using one worker:

```sh
python -m sqlfluff fix <declared SQL paths> --dialect=postgres --exclude-rules=AL07,LT05,LT12 --processes 1 --show-lint-violations --disable-progress-bar --nocolor --ignore-local-config --config /app/workload/driver/autofix.cfg
```

3. **Snowflake lint:** lint every declared original SQL file once, using the declared protected Jinja configuration, one worker, JSON output and no local configuration discovery.
4. **BigQuery parse and syntax export:** the protected client calls `Linter.parse_string` with the raw templater and exports every complete `to_tuple` tree with raw text, metadata and positions. Retain source/templated text, variants, diagnostics, exceptions, settings and version. This is syntax export, not lineage.

Copying, authentication, shared-metadata restoration, pre-reading and initial snapshots are untimed. Startup, imports, configuration, I/O, parsing, rules, repairs, serialization and completed writes remain timed through process exit. Each process has fresh home/cache/temp directories and the fixed environment.

Compare complete stdout, stderr and exit status. Lint JSON excludes only top-level per-file `timings`, preserving ordered records, nested fields, statistics, fixes and diagnostics. Malformed output remains observable. Original lint warning normalization is unchanged. New units preserve raw stderr.

Auto-fix also compares every final file byte, type, mode and file link count. Preserve non-target metadata except access times and replacement-affected parent-directory timestamps/size. Replacements retain modes and operation-user ownership. Directory link counts and other machine-specific metadata are checked within each operation, not against another host. The Snowflake lint and BigQuery export units must leave their complete input trees unchanged apart from access times.

## What you may edit
In `/app/sqlfluff`, edit only `.py` files under `src/sqlfluff`, except `src/sqlfluff/utils/testing/*`. Limits: 1,000 files and 20,000,000 bytes. Everything else is protected. Add no dependencies. Preserve attribution.

## Build and check
The image contains pinned source, dependencies and public inputs. Rebuild and check all public units and upstream tests:

```sh
sh /app/workload/build.sh
/app/build/run
/app/baseline-build/run
/app/build/run --outputs /tmp/sqlfluff-results
cd /app/sqlfluff
python -m pip install --no-deps --no-index --no-build-isolation --target /tmp/sqlfluff-test-plugin plugins/sqlfluff-plugin-example
SQLFLUFF_TESTENV=1 PATH=/app/build/runtime/bin:$PATH PYTHONPATH=/tmp/sqlfluff-test-plugin:/app/build/runtime python -m pytest test /app/workload/driver/test_autofix_contract.py --rootdir=. -c pyproject.toml -n 2
```

## How it is evaluated
A separate clean environment rebuilds your editable source, checks the edit scope, runs the project's tests, and compares outputs on public inputs and hidden inputs of the same kind using the contract above. It then times the original and your version on the hidden inputs, checking every output. Your score is a conservative estimate of the wall-clock speedup on the hidden inputs; when there are several timed units, each counts equally. Total CPU time and peak memory are reported but not scored.

## You may
- Change any code in the editable paths, including algorithms, data structures and internal interfaces.
- Add to the public interface, as long as every existing use keeps working.
- Use all 8 CPUs and 16 GiB, as long as results do not depend on how many CPUs are available.

## Rules
- Preserve all supported behavior, validation, errors and the public interface; compatible additions are allowed.
- Do not modify, remove or weaken existing tests. Changes outside the editable paths are ignored.
- No caching across calls or processes, no input or evaluator recognition, no precomputed results, and no delegation to other libraries or programs.
- Do not change process-wide settings, including GC, allocator, environment, signal handlers, locale, working directory, resource limits or CPU affinity.
- When a public call returns (or the program exits, for command-line workloads), nothing it started may still be running.
- Do not change the workload's fixed settings, flags, inputs, operation order or measurement boundaries. Do not defer required work beyond the measured operation.
- Results must be the same on every run, on any number of CPUs, and across repeated calls in the same process.
- Overlapping or aliased arguments, and short or failed reads and writes, must behave as in the original.
- No memory errors, undefined behavior or crashes, including on valid inputs the workload does not use.
- Write only within declared scratch/output directories.
