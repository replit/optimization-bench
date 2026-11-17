# Agent build context

This directory is the complete agent Docker context; it never contains verifier or held-out data. The Dockerfile's build-time guard authenticates small Git inputs and acquires larger public inputs from the exact upstream recipes in `payload-public.json`. Source/member bytes and every final input are checked against size/SHA-256 before use. Missing inputs and unavailable or changed upstream bytes fail closed with a clear diagnostic.

The pinned implementation is initialized as the asserted deterministic Git tree/root commit. Dependencies are acquired only during image construction. `workload/build.sh` rebuilds editable source offline and creates the thin `/app/build/run` wrapper; public local runs have no timer. All workload execution uses byte-identical shared runner code and `workload/task.py`.

The prebuilt image on Public ECR is pinned by digest in `task.toml`. Images are published only after an image-layer audit; build stages and caches are never published.
