# Adding a task

1. Follow the required tree in [task-format.md](task-format.md); there is no
   special per-batch task format. Pin the original source, dependency closure,
   input bytes, expected results, edit policy and upstream suite.
2. Implement only `task.py`'s adapter and necessary protected library glue.
   Copy `tools/runner/runner.py` byte-for-byte. Use the shared resident protocol
   for in-process APIs, or shared process steps for CLIs; never add timers.
3. Initialize and assert the deterministic source Git tree/root commit in the
   Dockerfile, then build the pristine source and check every public output.
   Keep all builds offline after dependency staging and honor source replay.
4. List every required public/private payload in `payload.json`. If original
   files are unavailable, mark the precise missing files and commitments.
   Use null commitments only when genuinely unknown, with a recovery reason.
   Install the common public guard without exposing held-out manifests.
5. Provide separate verifier settings, the held-out manifest/lock, and an
   upstream identity/outcome gate. Preserve source policies exactly.
6. Stage original files with `tools/stage_payload.py`, generate the catalog
   with `tools/update_catalog.py`, and run `tools/check_tasks.py`. For
   public-ready selections also run `--require-ready`.
7. Run tool/judge unit tests, both-executor public
   parity and no-op/negative controls on rebuilt images. Retain current receipts
   and explicitly label unavailable checks. At most two eight-core workloads
   may run simultaneously.

Do not describe payload readiness as scored qualification or a speedup.
