# Two-translation-unit runner calibrations

These are authored synthetic fixtures, not jq/CharLS/XZ/LAME source, not a
task's supported-standard inventory, and not mutation-qualification evidence.
The validation host needs `cc` and `c++`; no network or library download occurs.

Compile `first.c` and `second.c` **separately**, selecting their include with
`-DOPTIMBENCH_CONTROL_HEADER='"public.h"'` and `-I<fixture directory>`.
Compile the fixed C `library.c` separately as well. Link all three objects into
one executable, using the C++ linker for C++ consumers. Both consumers use a
volatile function pointer; do not enable LTO, garbage-collection/unused-section
tricks, `-fgnu89-inline` outside the declared dialect or multiple-definition
allowances.

Fixture-supported cells (a property of this fixture's own source, not a
declaration about any upstream task):

- `public.h`: gnu89, c99, c11, c17, c++11 and c++17 pass compile, link and run.
- `gnu-inline-mutant.h`: both TUs compile in gnu89, but the combined link fails
  with multiple definitions; c99 with the fixed out-of-line C library links.
- `linkage-mutant.h`: both TUs compile in c++17, but lose the public C linkage
  and fail to resolve the fixed C library on link.
- `umbrella.h`: valid legal include context, same public API; passes.
- `detail.h` directly: intentionally invalid include context, fails pristine
  compilation. It is a precondition/control failure, **not** a candidate finding.

There is intentionally no fixture claim that jq supports gnu89 or that CharLS
supports c99. Task owners must bind each actual header/dialect/feature cell to
pinned upstream evidence. In particular, pinned CharLS README's support matrix
requires C and C++ >=17; older-standard failures do not automatically qualify
as candidate regressions.

These calibration tests check compiler/linker phase observability. The canonical
Framework 2 runner, real public-header inventories and shared gate binding remain
separate required integration evidence.
