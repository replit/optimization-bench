# Frozen native formatting workload

`batch` and `native-batch` are two equally weighted timed units, not
per-document scores. The original CLI batch contains 89 public cold
invocations; its held-out counterpart contains 302. The native batch formats
844 public documents or 2,470 separate held-out documents once each. Both
entry points link the same submitted source. Neither unit is optional.

The protected caller invokes the documented `format.Format(io.Reader)` API.
One cold process decodes the complete request, formats every document, retains
all returned strings and errors, serializes the complete ordered result,
flushes stdout and exits. These operations are timed; input acquisition,
authentication, preparation, comparison and cleanup are not. Native stdout,
stderr and exit status are compared exactly. Null and empty errors differ.
The original CLI framing and normalized-stderr contract remain unchanged.

## Data and licenses

The frozen edition is `native-origins-v2`, with 17,584,937 public input bytes
from PostgreSQL/PostGIS and 6,164,010 held-out input bytes from Mozilla
bigquery-etl. The public and held-out native sets have no exact body-hash
overlap. This is not a claim of semantic uniqueness across SQL revisions or
other tasks.

`native-corpus.json` records immutable commits, archive size/SHA-256 pins,
licenses and exclusions. `native-public-origins.json` and
`../../heldout/native-origins.json` record each member's original path and
digest. Public acquisition recipes are in the task's `payload.json`;
held-out input/reference bytes are shipped separately through scoped LFS or
a hydrated release export. See [public reproduction](../../README.md).

PostgreSQL's license and PostGIS's original GPL/file-specific notices,
including the MIT license of PostGIS's `extensions/address_standardizer/`, are
in `environment/notices/` at the task root. Mozilla's MPL-2.0 notices are in
`notices/` beside this document. All attribution and source identities remain
with the frozen files. The PostGIS OGC suite remains excluded because its
separate redistribution grant was not authenticated. Do not add it back.

`native-public-reference.bin` (25,993,649 bytes, Git LFS) and the held-out
`tests/heldout/references/native-batch.bin` are the frozen formatter outputs.
They are modified (reformatted) copies of the source files and stay under each
source's license. Their derivation and terms are in
[notices/NATIVE-REFERENCE-OUTPUTS.md](notices/NATIVE-REFERENCE-OUTPUTS.md).

One public and four held-out inputs return formatter errors; their complete
original outcomes are intentional. The oracle is the pinned formatter's
output, not an independent database-semantic oracle. No input is rewritten,
repeated for padding, or filtered according to candidate results.

## Reproduction and controls

`native-source.lock.json` authenticates all 130 original source files.
`native-reference-build.json` is the original reference provenance binding,
not a qualification receipt for a newly built image. Expected results and all
source/input commitments remain unchanged.

From the repository root, with locked host dependencies installed:

```sh
python3 -m unittest tools.tests.test_sqlfmt_native
python3 -m unittest discover -s tasks/sqlfmt-cli/tests/fuzz -p 'test_*.py'
python3 tasks/sqlfmt-cli/tests/fuzz/native/native_driver_tests.py
```

The caller test needs Go 1.26.9 and authenticates the public original source.
For current body-free images, follow the input preparation, source-only export,
canonical `judge.score --gates-only`, held-out admission and verifier-owned
negative-control commands in [public reproduction](../../README.md). Reuse
authenticated cached inputs for offline checks, and add `--stop-on-gate` to
the gates-only command for fail-fast engineering validation.

`native_validate.py` is a legacy helper for input-bearing images: it copies
`/app/workload/inputs` from the image and assumes those bodies are present.
It is not the reproduction path for current body-free images. Its `slower`
control inserts `time.Sleep(3 * time.Millisecond)`, a correctness-preserving
slowdown only, not an improving-source control or evidence of gain sensitivity.

Use the separate held-out admission and negative-control commands in
`tests/README.md` for all 302 held-out CLI invocations and 2,470 native
documents. These correctness controls do not qualify timing or a speedup.
Historical profiling/campaign output is deliberately not part of this package.
