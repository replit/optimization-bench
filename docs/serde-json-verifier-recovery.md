# Restore the original serde_json verifier files

The scorer-only `scorer-metadata/serde-json-production-crate-index-ingestion/`
bundle holds seven byte-identical authored metadata originals at their existing
`tests/heldout/...` relative paths. It is outside the agent environment and is not
an input or golden-body redistribution. The six data-containing legacy goldens
are recovered outside the repository against their original lengths and
SHA-256s, never
replaced by new expectations. All original task files remain unchanged.

The original canonical SDK image already contains `/app/baseline-build/prog`,
built from pinned pristine source and checked against public commitments during
the ordinary image build. That step does not require the retained verifier
files. Recovery uses this fixed image-owned producer, not candidate/agent source,
artifacts, a caller-supplied program or a new reference qualification. The normal
thirteen-file retained authentication before judging is not bypassed.

On an eight-CPU verifier host, with that canonical SDK image already local:

```sh
python3 tools/recover_serde_json_goldens.py \
  --image "$SERDE_IMAGE" --cpuset 0-7 \
  --inputs /tmp/original-verifier-inputs/files --output /tmp/serde-originals
python3 tools/stage_payload.py --task serde-json-production-crate-index-ingestion \
  --scope heldout --from /tmp/serde-originals
```

`--inputs` is an external directory containing the original SHA-named input
files. If those bytes are genuinely absent, replace it with explicit
`--fetch --cache /tmp/serde-original-input-cache`: only the six already-declared
upstream inputs, totaling 2,896,102 bytes, may be acquired. Recovery never acquires
either complete corpus. Inputs are authenticated before the read-only producer
mount; exact strict JSON canonicalization uses the existing protected routine.
All six frozen output hashes must match before the fresh external thirteen-file
`--from` directory is emitted. Missing/corrupt bundle originals, pointers, changed
inputs, producer failures or changed outputs fail closed without updating pins.
The existing staging command alone fills the original task destinations.

Never add the recovered goldens or external input cache to Git, LFS, source
exports or agent images. This is untimed exact-byte recovery, not a performance
result, source/image publication or a license grant. The seven-original bundle
and this recovery path must both be present in the public source; access to a
private origin or LFS store is not required.

This scorer-only preparation restores the original bytes before ordinary
admission without changing the task's files.
