# Native Terser correctness hooks

These untimed hooks retain the existing shared gate contracts. They do not change
the timed drivers, minification options, input/output commitments, sampling,
source edit scope or scoring policy.

## Coverage

- `rc_hooks.py`, `rc_driver.py` and the JavaScript fixtures exercise both package
  import/require entries and `minify`/`minify_sync` in actual long-lived processes.
- Repeat tests compare fresh observations with forward/reverse repeated calls.
  Aliasing tests reuse the caller's real `nameCache`, interleave an independent
  cache and syntax error, and compare output and cache mutations.
- Callback tests throw actual EPIPE/ENOSPC-labelled errors from `format.comments`
  and observe error identity, partial callback events and recovery. They are
  callback faults, not invented OS-stream support.
- `rc_cli.py` and `native_cli.py` expose the real upstream `bin/terser` to the
  shared CLI observer: closed stdout/reader, `/dev/full`, readonly output
  directories and non-root execution. Do not substitute prewritten observations.
- Facts bind the actual candidate/build digest, PID/start time, module-load and
  request counts, source/bundle hashes and in-process CPU affinity. The shared
  observer/verdict code decides pass or rejection.
- `header-scope.json` authenticates the pinned 324-file upstream archive and its
  header/native-consumer scan. Terser has no C/C++/native-header ABI to exercise;
  the hook reports that evidenced scope rather than inventing a header gate.
- `process-state.test.mjs` covers the separate process-state adapter.

The CLI and library fixtures are bounded regression controls, not exhaustive
upstream API or CLI-option coverage. The protected upstream suite, complete
public/held-out outputs and differential corpus remain required.

## Focused tests and native calibration

```sh
python3 -m unittest discover -s tasks/terser-minify/tests/fuzz -p 'test_*.py'
node --test tasks/terser-minify/tests/fuzz/process-state.test.mjs
UV_THREADPOOL_SIZE=8 python3 tasks/terser-minify/tests/fuzz/native_calibration.py \
  --baseline /absolute/path/to/build --node /absolute/path/to/node \
  --out /fresh/output/directory
```

The calibration requires eight real available CPUs, executes at 1/2/4/8 CPUs,
and builds five disposable broken-source controls: semantic output, state counter,
aliased cache, swallowed callback error and file-descriptor leak. Each must be
rejected by its intended shared gate. `--cli` additionally requires real local
mount namespaces; the ordinary judge instead uses the hardened Docker transport.
Do not use extra image capabilities merely to make that optional local route pass.

The calibration is untimed, emits no score or host qualification, and is not a
substitute for a real exported-source candidate rebuilt through the common judge.
See `../README.md` for acquisition, separate-verifier commands and licensing.
Historical campaign logs, profiler bundles and old timing receipts are excluded
from this release package.
