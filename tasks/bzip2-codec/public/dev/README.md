# Public files and workload

The 24 intact files total 10,275,437 bytes. The corpus is a file-type coverage
sample, not a popularity-weighted estimate of all compression workloads.

| Family | Files | Public origin |
|---|---:|---|
| XML documents | 1 | Federal Register issue |
| CSV tables | 1 | NOAA Storm Events locations |
| JSON metadata | 4 | Canadian open-data climate metadata |
| Source and configuration | 12 | Kythe |
| Browser assets | 3 | uPlot |
| Numerical recordings | 3 | BIDMC records 01 and 08 |

[manifest.json](manifest.json) records every source URL, version, transformation,
raw hash and byte count, family, collection and split group. Each file also has
reference streams at block-size settings 1 and 9 with exact hashes. Original
notices are mapped by [NOTICE-INDEX.json](NOTICE-INDEX.json); retain them and
[ATTRIBUTION.md](ATTRIBUTION.md) with materialized inputs. Source metadata retains
any original attribution-review status; this package makes no new licensing
determination. The recording-header field is an upstream provenance reference,
not a path to a separately packaged input.

The current `distinct-natural-workload` timed unit performs all 96 distinct
compress/decompress operations over these files once in one protected native
process. A separate `small-memory-text` unit decodes 75 complete additional
block-9 text streams with `small=1`. All original individual operations and
guards remain mandatory correctness cases. [The workload plan](../workload.json)
is retained historical collection/calibration metadata, not the active timing
driver: its family repetition counts and old `--calibrated` option are not used.
The active contract is in `environment/workload/task.py` and `tests/judge.json`.

The shared corpus assigns related records and alternate representations to the
same side of the split. Held-out evaluation uses different files, including
related collections and entirely held-out collections in the same broad families.
Their identities and data are not in this public development package; they are
kept under `tests/` with their own attribution bundle.

Synthetic fixtures in [the public guard manifest](../regressions/manifest.json)
are correctness-only and never scored. They cover empty and boundary-size
inputs, tiny buffers, both decoder modes and damaged/truncated/trailing streams.
The protected wrapper rejects concatenated or trailing streams with status -100;
the library's broader streaming API remains covered independently by API tests.
