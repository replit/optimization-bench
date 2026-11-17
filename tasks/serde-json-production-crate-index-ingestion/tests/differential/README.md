# Verifier-only coverage

Frozen before any candidate patch: value parsing/serialization, streams/offsets,
borrowed strings, JSON pointer escapes, i128/u128/float boundaries, malformed
UTF-8/JSON/surrogates, empty/large/Unicode data, repeated calls, returned-value
mutation and interleaved independent objects; complete real public registry
records and bounded schema-preserving/malformed mutations.

The protected Rust probe links the exact admitted production rlib. It and the
generator/runner are mounted read-only only by the separate verifier. None is
present in the worker context or instruction. Supported nondefault feature and
generic API coverage is complemented by the identity-bound upstream matrix.
Default three seeds are 1729,2718,31415; 30 cases each, no case filtering.
No held-out instances, optimizer traces or solution patches are used.

## v1.1 writer-fault reference slice

`writer_probe.rs` exercises four public serialization entrypoints with a legal
`std::io::Write` sink: successful/short writes, error before the first byte,
partial-then-error, write-zero, one-shot EINTR and a failing flush method.
`generate.py` adds one scheduled writer case to each existing differential case;
the outer seeded budgets remain unchanged. Complete accepted bytes and
error category/I/O kind are compared; panic presence and abnormal process exits
are observations. Unpromised write-call chunk sizes/counts are diagnostics.

The report channel is formatted independently of the candidate serializer:
a broken `to_writer` cannot erase its own observation by breaking report output.
The probe does not assume serialization flushes a writer automatically.

`validate_writer_reference.py` runs the fixed corpus twice against pristine,
then builds three disposable pristine-source mutants (panic on writer error,
swallow writer error, discard writer output). It saves original/minimized cases
and exact replay receipts. Run it only on an isolated scratch host:

```sh
docker run --rm --network none --cpuset-cpus 0-7 --memory 8g \
  -v "$PWD/tasks/serde-json-production-crate-index-ingestion/tests/differential:/verifier-differential:ro" \
   -v /tmp/writer-validation:/scratch optimbench:serde-json-production-crate-index-ingestion \
  python3 /verifier-differential/validate_writer_reference.py --out /scratch
```

The image must be built from this task's unchanged pinned `environment/`.
The frozen corpus and minimized reproducers under `writer-reference/` are
regression fixtures, not current release qualification or coverage of every
serde_json export. Shared source/interface, upstream, native process-state and
complete public/held-out correctness gates remain independently required.
