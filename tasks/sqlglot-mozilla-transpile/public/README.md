# Public development material

`dev/manifest.json` lists 284 unchanged Mozilla BigQuery ETL queries, with their original paths and SHA-256 hashes. `dev/inputs/` contains only that development split. Run every query once per complete batch in manifest order. Inputs are already frozen; do not resample, edit, or delete them.

`probes/cases.json` contains 38 independent generic regression inputs. `probes/api-cases.json` contains 11 additional tokenizer, AST, dialect and option probes. These are inputs only; the trusted evaluator retains baseline responses separately. The protected upstream SQLGlot tests are also available in the reconstructed source tree.

Mozilla inputs retain their MPL-2.0 notices; see `dev/LICENSE.mozilla`. Held-out cases and their expected outputs are not part of this development package: they ship under `tests/heldout/`, with input and expected-output bodies in Git LFS, outside the agent image. The TPC-H/TPC-DS-derived public optimizer, lineage and diff inputs are fetch-only and are derived locally from SQLGlot's pinned repository; see `optimizer/README.md`.
