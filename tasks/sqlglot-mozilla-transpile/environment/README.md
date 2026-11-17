# Agent build context

This directory retains the original source, compiler and guard contract. `public-input-packet.json` declares exact read-only runtime data roots and any original pinned public derivation. Use the shared public runtime packet helper before agent startup; acquisition archives are not automatically visible. The generated agent command is agent-only. Genuine correctness and performance judging runs separately from policy-admitted source exports. Audit all image layers and metadata before publication; local acquisition does not grant redistribution.

The pinned implementation is initialized as the asserted deterministic Git tree/root commit. Dependencies are acquired only during image construction. `workload/build.sh` rebuilds editable source offline and creates the thin `/app/build/run` wrapper; public local runs have no timer. All workload execution uses byte-identical shared runner code and `workload/task.py`.

The prebuilt image on Public ECR is pinned by digest in `task.toml`. Only the final input-free image is published, after an image-layer audit; build stages, caches and input-bearing images are never published. Supply the authenticated runtime inputs through the declared read-only mounts.
