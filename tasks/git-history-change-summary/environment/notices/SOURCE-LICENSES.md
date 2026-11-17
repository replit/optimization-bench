# Pinned source and history

Git's complete corresponding source is retained at `/app/git`, including the
original COPYING and per-file notices. Git is GPLv2; xdiff's original
LGPL-2.1-or-later notices are retained. No permissive relicensing is claimed.

The public input histories are Git, Cargo, Django and Prettier. Each is
fetch-only, not a retained corpus in this repository, and is authenticated by
the exact semantic inventories. Original license and per-file notices remain
in the original histories and checkouts: Git GPL/per-file notices, Cargo
MIT/Apache-2.0 plus original third-party notices, Django BSD-3-Clause plus
original Python notices, Prettier MIT. These root labels do not relicense
every historical third-party file. Original checkout attributes, historical
warnings and uninitialized gitlinks remain part of the declarations.
Later units add further fetch-only public histories under the same basis:
Laravel (MIT), HAProxy (GPL-2.0 with LGPL-2.1 headers), Vue core (MIT), libuv (MIT)
and Sphinx (BSD-2-Clause). Their original license and per-file notices likewise remain
in the original histories and checkouts; the root labels do not relicense every file.

HAProxy (https://github.com/haproxy/haproxy, commit
`463527ba1e19c4fafae96fd16d1ff56ce979a698`): GPL-2.0 for the core, LGPL-2.1 for the
exportable include files, with an exemption allowing OpenSSL. Its `LICENSE` is copied
verbatim from that commit to `haproxy-LICENSE` here; the GPL-2.0 and LGPL-2.1 texts are in
`COPYING` and `LGPL-2.1` here and in the history's own `doc/gpl.txt` and `doc/lgpl.txt`.

This image contains no held-out data. The task's held-out set is distributed separately
with the repository's verifier files, not in this image.

## Obligations when redistributing this image

- Git (GPL-2.0-only; xdiff and some compat/ code LGPL-2.1-or-later): the task's Git build
  (`/app/baseline-build`) is built in the image from the complete corresponding source at
  `/app/git`, which keeps `COPYING`, `LGPL-2.1` and the per-file notices. Keep that source
  and these notices with the image. Anyone who distributes a modified build (for example
  with a candidate xdiff change) must offer its complete corresponding source under the
  same licences. Operating-system packages from the Debian base image, including Debian's
  own `git`, keep their notices under `/usr/share/doc`.
- Input histories: they are complete, unmodified source histories with their own licence
  files and per-file notices. Keep them unmodified and keep those files. For GPL/LGPL
  histories (Git, HAProxy) the history is itself the complete source; no binary is built
  from them.
- Keep this directory (`/app/notices`) with any copy of the image.
