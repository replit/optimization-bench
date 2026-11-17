# Column lineage and structural diff

Both are separate, equally weighted performance units in the existing task and image. They use the same rebuilt SQLGlot package as conversion and optimization. The shared runner, scoring policy and weights are unchanged.

| Unit | Public | Held-out | Sources |
|---|---:|---:|---|
| Column lineage | 125 queries | 48 queries | Public SQLGlot/SQLMesh (TPC-derived queries fetch-only), held-out Mozilla |
| Structural diff | 99 pairs | 18 pairs | Public TPC-DS originals/expected queries (fetch-only), held-out Mozilla revisions |

Each distinct input runs once per unit. Sources can recur across workloads. Separation is between public and held-out origins within each workload, not between all workloads.

The original held-out diff probe had seven file pairs at two revisions. Those pairs remain unchanged. Eleven compatible changed files from the next 100 older SQL-directory commits were added for margin above a 1.3-second sandbox target. The final 18 files are distinct and span three pinned repository revisions. All compatible files in that window were considered, without selecting by duration. No SQL or schemas were invented or repeated.

## Complete results

Lineage preserves ordered roots and edges, node sharing, payloads, semantic expression classes, ordered children, types, comments and metadata. It follows the reviewed parse/copy/qualify/scope/per-column call pattern. Allocation identity is not output.

Diff preserves normalized SQL, every typed edit and its source/target path, complete semantic trees, node counts and similarity scores. Only incidental edit-list order is sorted. Source dialects are explicit. The internal edit costs used to produce the similarity result are not benchmark unit weights.

Errors, logs and warnings are part of both contracts. Every result is fully materialized and written before exit. Expected lengths and SHA-256 cover the complete result bytes, not a projection. Held-out complete result bodies are stored with Git LFS under `tests/heldout/`. Machine-specific file state is not part of expected outputs.

The frozen contract identifiers retain their earlier `scratch-` prefix so the reviewed result format is not silently renamed. These are now scored task units, not optional diagnostics.

## Adoption and checks

`adopt.py` verifies the reviewed packet hashes and complete pinned baseline source inventory. Run it with `PYTHONHASHSEED=0 python -P`, matching scored execution. Isolated Python ignores the seed and is rejected. The author plans all writes before publishing, preserves every existing workload commitment, and rejects replacement of an already frozen family. `--freeze` is required for first adoption. Subsequent calls verify without writing.

Public source commitments are in `provenance.json`. Held-out origins and source revisions are recorded in `tests/heldout/alternates-provenance.json`, which ships with the held-out set (input and expected-output bodies in Git LFS) outside the agent image, not in agent instructions or public logs. Source packets are trusted authoring inputs, not candidate output. The source-selection code is documented by the frozen provenance and the reviewed source recipe.

The TPC-derived public lineage and diff inputs are fetch-only: see `public/optimizer/README.md` and `environment/public-input-packet.json`. Expected-output commitments are unchanged.

`test_workloads.py` checks all four units against complete commitments, then measures each new unit in three cold processes and one profile. It rebuilds deliberately wrong, skipped and slowed versions of each new API. Wrong and skipped results must fail, omitted outputs must fail, and slowed correct results must match and take longer.

Ownership uses cProfile Python-level self time, accepted by the task reviewer. The approved ratio is repository divided by repository plus third-party source time. The strict ratio includes all recorded self time. This is not native CPU attribution. Sandbox checks do not establish GCP timing stability or a speedup.

## Research A/A

`optimizer_noise.py --units lineage diff` extends the earlier optimizer-only helper. It verifies complete correctness for every original and new case once, then samples just the selected new units using the unchanged shared A/A settings. Its output explicitly says this is unofficial, not whole-task qualification.

The unchanged original held-out conversion has previously measured 0.822 seconds locally. Its one-second task floor remains intact. Selecting new units in this research helper does not skip, reweight or relax that unit in the task.
