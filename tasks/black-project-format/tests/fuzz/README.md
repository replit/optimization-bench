# Black verifier adapter

This is verifier-only code, not part of the agent image or the timed workload.
`adapter.py` defines canonical cases, preconditions, shrinking and full
observation comparison. `worker.py` imports the actual compiled Black module;
`persistent_worker.py` also observes repeated calls and lifecycle state.

`rc_hooks.py` registers:

- Physical CLI reads/writes and failing/closed streams for small and large
  real inputs, including in-place read-only directories.
- Public API stream errors, independently mutable `Mode` instances, and
  repeated A/B/A/B calls compared with fresh native oracles.
- A source/archive-bound no-public-C-header applicability proof, not a waiver
  of native import, lifecycle or instrumentation checks.

The shared judge owns process supervision, subprocess faults, CPU-affinity
evidence and gate classification. An adapter registration or a Python
syntax check is not native gate qualification.
The inventory generator requires an independent public-source scope review
bound to the exact archive, file count and complete file-inventory digest
before it will regenerate either inventory report.

Run the cheap contract tests from the repository root:

```sh
python -m unittest discover -s tasks/black-project-format/tests/fuzz -p 'test_*.py' -v
```

For a fresh native self-replay, provide a build produced by the protected
task build, the matching image and an output directory outside the checkout:

```sh
python tasks/black-project-format/tests/fuzz/smoke.py \
  --image optimbench:black-project-format --build /absolute/path/to/native-build \
  --cpuset 0-7 --out /absolute/path/to/replay
```

The default is 18 deterministic cases with two fresh workers per case.
The report binds the native module origin, compiled flag, build identity and
harness hashes. `mutants/format-str-identity.json` describes a disposable
source-level semantic control: it must be rebuilt through the unchanged
native recipe and rejected on complete returned output. It is not a submitted
optimization, an instruction to modify protected goldens, or a substitute for
the full shared gates.

See [`../README.md`](../README.md) for the clean-checkout, source-export,
scorer and held-out positive/negative-control commands. Historical campaigns
and receipts are deliberately not included in this release package.
