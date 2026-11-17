# Neutral agent prompt template

The fenced template is the source for the shared sections of every task
instruction generated in the v1.0 prompt format. It describes resources and
behavioral rules without
promising optional measurement or debugging tools.

Goal, the base Setup paragraph, How it is evaluated, You may, and the shared Rules bullets
(every bullet above the task-specific placeholder) below are identical across
every prompt generated from this template. Only factual workload, comparator,
edit-scope, commands and necessary additional restrictions vary. Use 350–800
words of prose per prompt. A task that declares more than one public
performance unit in `tests/judge.json` may add 40 words per extra unit, up to
1,400 words, so each workload can be stated factually. Describe closed
editable sets using exact rules expanded against the pristine source
inventory, or enumerate literal filenames inline. Do not infer a broader
editable rule without proving equivalence.

````markdown
# Make <project> faster

## Goal
Reduce the wall-clock time of the workload below while preserving every observable result under the stated output contract.

## Setup
8 CPUs, 16 GiB memory, no network access, and a 7,200-second agent timeout (2 hours). The source you leave in the editable paths when time runs out is what gets evaluated.

## Workload
<What is called, input population, fixed options, what is timed and excluded,
complete output comparator, and supported behavior. Facts only.>

## What you may edit
<Source checkout, explicit roots/extensions/exclusions or inline filenames,
source limits, protected files and dependencies.>

## Build and check
<Existing image-local build, public check and upstream test commands.
Do not reference verifier-only scripts as local commands.>

## How it is evaluated
A separate clean environment rebuilds your editable source, checks the edit scope, runs the project's tests, and compares outputs on public inputs and hidden inputs of the same kind using the contract above. It then times the original and your version on the hidden inputs, checking every output. Your score is a conservative estimate of the wall-clock speedup on the hidden inputs; when there are several timed units, each counts equally. Total CPU time and peak memory are reported but not scored.

## You may
- Change any code in the editable paths, including algorithms, data structures and internal interfaces.
- Add to the public interface, as long as every existing use keeps working.
- Use all 8 CPUs and 16 GiB, as long as results do not depend on how many CPUs are available.

## Rules
- Preserve all supported behavior, validation, errors and the public interface; compatible additions are allowed.
- Do not modify, remove or weaken existing tests. Changes outside the editable paths are ignored.
- No caching across calls or processes, no input or evaluator recognition, no precomputed results, and no delegation to other libraries or programs.
- Do not change process-wide settings, including GC, allocator, environment, signal handlers, locale, working directory, resource limits or CPU affinity.
- When a public call returns (or the program exits, for command-line workloads), nothing it started may still be running.
- Do not change the workload's fixed settings, flags, inputs, operation order or measurement boundaries. Do not defer required work beyond the measured operation.
- Results must be the same on every run, on any number of CPUs, and across repeated calls in the same process.
- Overlapping or aliased arguments, and short or failed reads and writes, must behave as in the original.
- No memory errors, undefined behavior or crashes, including on valid inputs the workload does not use.
<Additional task-specific restrictions, only where necessary.>

````

## Design notes

- Prompts contain no strategy suggestions, optimization-method vocabulary or
  effort coaching. Apart from the explicit permission to use all 8 CPUs and
  16 GiB, resource facts do not prescribe how an agent uses them.
- How it is evaluated states what is scored: a conservative wall-clock
  speedup, with units weighted equally; CPU time and memory are reported only.
  Prompts do not invite agents to add tests.
- Lifecycle requirements use a neutral completion rule rather than
  language-specific advice.
- Prompts contain no text copied from other tasks, version history, host or
  qualification notes, internal contract labels or references to inaccessible
  metadata.
- Task-specific options, exact edit policies, size caps and output comparators
  are preserved, and workloads are described from their definitions and
  drivers.
- CLI diagnostics state plainly that error messages/classes and exit status
  must match, while stack traces and memory addresses may differ.
- Exact source rules (for example, for SQLite and goldmark) are used instead of
  inventory appendices; small literal sets remain inline. There are no
  prohibited-term exceptions.
- The shared You may section states the permitted scope. The shared Rules
  bullets state the determinism, CPU-count, repeated-call, aliasing, I/O-fault
  and memory-safety checks the judge runs. Tasks whose C-header or
  command-line checks apply carry one task-specific bullet each.

NumPy sets OMP_NUM_THREADS=8 and OPENBLAS_NUM_THREADS=8 in its Dockerfile and
workload ENV; these are functional workload settings.
The catalog generates the shared sections directly from the fenced template
above for every task in the v1.0 prompt format. Legacy-version members keep
their original prompt format (see [task-format.md](task-format.md)).
Prompt validation checks shared-section parity, resource facts, word counts,
prohibited wording and exact closed editable rules. The current judge restores
protected out-of-scope edits and records their review flags; prompt validation
alone does not execute or prove that behavior.
