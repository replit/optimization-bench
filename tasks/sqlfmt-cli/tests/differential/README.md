# CLI differential gate

The unchanged formatter and the rebuilt candidate execute the same deterministic
cases under the protected Docker executor. Seeds 1729, 2718 and 31415 each
generate 24 cases, covering all eight combinations of `-l`, `-d` and `-w`,
stdin/single-file/tree transports, Unicode, malformed SQL, empty input, wide
select lists, help/version/errors and real public SQL recombinations.

The generator reads only documents named in the frozen original CLI manifest,
authenticates their size and SHA-256, and preserves the original sorted
content-address order. Raw native-batch documents share the storage directory
but do not have CLI framing and must not enter this pool. They remain separately
mandatory in the native timed unit and native contract controls.

The driver compares complete observations, including files, stdout, exit status,
and `cli-stderr-contract-v1` normalization. A pristine harness error is not a
passing case. Mismatches are deterministically shrunk with a reproducer.
`judge.score --gates-only` runs this gate without timing calibration or scoring.
See [public reproduction](../README.md).
