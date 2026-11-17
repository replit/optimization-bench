# Input acquisition and usage restrictions

The task's source and output commitments are unchanged. Distribution has changed:
all public inputs are fetched from pinned publisher/CDN copies, and all held-out
inputs are acquired only by the separate verifier. No input bodies or minified
golden bodies are redistributed in this task package or in its published image.

`CDNJS-INPUTS.json` records the 149 public cdnjs source URLs, byte counts and SHA-256
values. The seven public ES-module files retain their original pinned npm archive
and member recipes and verbatim notices in `WEBPACK-ESM-INPUTS.md`. Held-out source
URLs and npm license evidence remain under `tests/fuzz/`, never in the agent image;
the held-out rows of the task's `payload.json` record an SPDX license expression and
its evidence for each held-out input.
Acquisition verifies the complete original bytes before execution; no source is
substituted, normalized or removed from the workload.

Public availability is not a license grant. Each library retains its own terms.
The cdnjs index's optional license labels are observations from publisher metadata,
not a conclusion that any particular use or redistribution is permitted. Preserve
the original notices and obtain any permission required for your intended use.
The Highmaps 6.0.3 input (cdnjs label CC-BY-NC-3.0) was removed from the public
batch on 2026-11-15. Do not assume commercial-use or redistribution permission
from anonymous access.
Other cdnjs libraries include GPL, LGPL, EUPL and unspecified license labels.

The ordinary image build authenticates its public inputs in a separate stage and
uses a transient read-only mount for its exact-output guard. Raw assets are not
copied into the final image. Operators acquire permitted original bytes at setup
and mount them read-only for execution, outside the measurement boundary.

Only the final, input-free image built from this recipe is published, on Public
ECR as `public.ecr.aws/h1f5v5u6/optimbench:terser-minify-1.0.0`, pinned by digest
in the task's `task.toml`. It contains no input bodies and no minified outputs; its
inputs are supplied at run time through the read-only mount. Publishing that image
grants no permission to redistribute the acquired inputs. Do not publish build
caches, intermediate stages, input-filled runtime containers or derived outputs
without independently satisfying the applicable terms.

The historical `recovered-requalified (not proven original)` lineage label is
retained; matching current publisher copies proves byte availability, not original
sampling provenance.
