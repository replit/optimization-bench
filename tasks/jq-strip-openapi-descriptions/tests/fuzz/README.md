# jq public-contract fuzz adapter

## Generic RC integration

`rc_hooks.py` registers native aliasing, stream-fault and repeat-in-process
observations in addition to the existing CLI cases. The shared judge still owns
all verdicts. The bounded C API probes cover parser chunks/malformed EOF, public
input-callback failure, compile/start/next/teardown and valid value-copy ownership.
They do not claim complete public API coverage or deep-hardening adequacy.

The persistent probe executes the same case list forward then backward, comparing
each call to fresh-process results. Every call records actual affinity, PID,
file descriptors, thread identities and child processes using the shared native
lifecycle observer. Probe failures are infrastructure errors, not passing rows.

Header consumers compile and link both installed headers in two translation
units for both C and C++, in the pinned compiler's default modes. This uses the
shared `compiler-default` support basis rather than inventing numeric dialect
promises from successful compilation. Source references bind the installed-header
inventory, upstream compiler discovery and explicit C++ linkage guards. The old
numeric-dialect inventory is retained as historical investigation, not a claim
that C89, GNU89 or any other numeric standard is newly qualified.

These are verifier/test changes, not task-image recipe changes. After merge,
restage the verifier and run fresh full qualification. Local probe calibration
does not replace pristine control or 1/2/8-CPU correctness on the campaign judges.

## Earlier deep-hardening work

Exclusive task-owned work under `tests/fuzz/`; protected production helpers,
existing differential cases, inputs and timing settings are unchanged.

This is **not full-task qualification**. `surface.json` is a 170-entry worklist
(133 header functions, 36 macros, one implemented CLI family), not the corrected
shared surface denominator. Most C entry points remain unmapped/unexercised.
Header dialect authority, sanitizer/race/changed coverage, mutation portfolio,
1/2/8-CPU five-repeat evidence and full public-option mapping remain incomplete.
No missing evidence is counted as a pass.

## Implemented slice

- Fixed seeds 1729/2718/31415; bounded grammar and explicitly supplied,
  hash-verified `public_dev` document transformations. No private corpus walks.
- Complete stdout/stderr bytes use base64; replacement decoding cannot merge
  distinct invalid UTF-8 outputs. Python emits the transport independently of
  candidate libjq. Error/exit presence is retained.
- Shrinks preserve surface/options/state/fault class. Unknown cases and malformed
  observations fail closed rather than comparing equal.
- Two distinct TUs for **both installed public headers** reference function
  addresses and actual calls. `headers/inventory.json` records source quotes
  and unresolved numeric dialect authority; it does not invent GNU89 support.
- `native/probe.c` exercises public parse, chunked parser, compile/start/next/
  teardown and valid value-copy ownership sequences. Its JSON envelope is
  libc-generated; public dumps are observed values, not the reporter encoder.
  This is only a narrow C slice, not all-C-API evidence.

Unit checks:

```
cd tasks/jq-strip-openapi-descriptions/tests/fuzz
python3 -m unittest -v test_adapter.py
```

Initial pristine calibration ran 154 CLI cases (64 grammar cases plus three
transformations of each of the 30 explicitly selected public documents) and
four narrow native smoke modes. It establishes driver feasibility only;
malformed JSON error returns were retained as legal observations. It is not a
candidate campaign, two-arm result, sanitizer result or mutation score.
GNU89 observations remain informational calibration only; they do not override
the strict declaration gate.

The declared full-campaign budgets in `adapter.json` match the approved jq group.
No full campaign or mutation-adequacy claim has been made. The shared framework
owns instrumentation, adequacy thresholds and final verdict semantics.

Optional allocator/TLS follow-up must bind the actual `config.h` and use legal
public callback states. The proposed handler records a fixed allocation-free
event and `_Exit`s; it must not longjmp across pthread boundaries or return NULL
directly from `jv_mem_alloc` (which would bypass its error handler). Arm only
during the tested public call, record whether the fault really fired in both
arms, and do not mistake different allocation counts/thread IDs for a contract
regression. Worker-handler hypotheses require fresh legal runtime evidence.
