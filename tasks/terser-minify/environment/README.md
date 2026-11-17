# Agent build context

This directory stages public inputs for the original build guards; it never contains verifier or held-out data. The Dockerfile uses transient read-only public input mounts. Stage and authenticate the public packet before building. Do not assume that the image contains runtime bodies. Before offline execution, supply the same authenticated files read-only at `/app/workload/inputs/files`; keep the manifest image-owned. Audit all image layers and metadata before publication; do not publish body-containing build contexts or caches.

The pinned implementation is initialized as the asserted deterministic Git tree/root commit. Dependencies are acquired only during image construction. `workload/build.sh` rebuilds editable source offline and creates the thin `/app/build/run` wrapper; public local runs have no timer. All workload execution uses byte-identical shared runner code and `workload/task.py`.

The prebuilt image on Public ECR is pinned by digest in `task.toml`. Only the final input-free image is published, after an image-layer audit; build stages, caches and input-bearing images are never published. Supply the authenticated runtime inputs through the declared read-only mounts.
