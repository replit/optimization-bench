# Held-out expected outputs: source licences

`tests/heldout/expected/` stores the pristine `git log` patch and shortstat
outputs of the held-out units. These outputs reproduce text from the four
reserved repository histories below, which are otherwise fetch-only. Each
repository's licence, retrieved at the pinned commit, is retained here.
Historical files may carry their own per-file notices, which the outputs
reproduce unchanged; these root licences do not relicense them.

| Repository | Pinned commit | Licence | File |
| --- | --- | --- | --- |
| https://github.com/neovim/neovim | 187d13aa6047e1914244e8033b2d2f3379a0b97f | Apache-2.0, with Vim-licensed portions | [neovim-LICENSE.txt](neovim-LICENSE.txt) |
| https://github.com/rust-lang/mdBook | 8c41b823bb093fd14303dc38809c78e0df2d53ea | MPL-2.0 | [mdbook-LICENSE](mdbook-LICENSE) |
| https://github.com/pallets/click | 2247b35ea1c47c727d7a06e51fa280e12a863ff6 | BSD-3-Clause | [click-LICENSE.txt](click-LICENSE.txt) |
| https://github.com/eslint/eslint | 9155e14781bf0a5549f9a747691f7d98c8c66dcd | MIT | [eslint-LICENSE](eslint-LICENSE) |

MPL-2.0 source for the mdBook text is available from the repository above at
the pinned commit.

The other six held-out histories are fetched from the repositories below at
verification. Only the length and SHA-256 of their expected outputs are stored
(`tests/baseline-outputs.json`), not output bodies, so no text from them is
redistributed here. Their licences, checked at the pinned commit, are recorded
for reference:

| Repository | Pinned commit | Licence |
| --- | --- | --- |
| https://github.com/expressjs/express | 1c6f1b0fe4000977b9a7b4f34097e4d527dd9824 | MIT |
| https://github.com/junit-team/junit5 | 71b18a51ae1007c527c71791306c09b02280d639 | EPL-2.0 |
| https://github.com/python/mypy | 033f9580d17838aef5c081b1dda912be813ada9b | MIT; `mypyc/lib-rt/pythonsupport.h`, `getargs.c` and `getargsfast.c` are PSF-2.0 |
| https://github.com/pypa/pip | 1000636317b856a7a8d0ae6662c3eb30c2a9bfed | MIT; vendored packages in `src/pip/_vendor/` carry their own licences |
| https://github.com/prometheus/prometheus | 3cb40f50066011274b4e54ca7944045851a67d0a | Apache-2.0 |
| https://github.com/sqlalchemy/sqlalchemy | 5c2970e9e5cb233ed530c274d96c73ebdbdbfc8a | MIT |

## Obligations when redistributing the expected outputs

- Keep this file and the four licence files with the outputs.
- mdBook (MPL-2.0): the outputs reproduce parts of MPL-covered source files
  unmodified. Keep `mdbook-LICENSE` (the MPL-2.0 text) with them and keep the
  pointer above to the pinned commit, where the source form is available.
- Neovim (Apache-2.0 with Vim-licensed portions), Click (BSD-3-Clause) and
  ESLint (MIT): keep the licence texts and the copyright and per-file notices
  that the outputs reproduce.
