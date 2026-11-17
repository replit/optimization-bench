#!/bin/sh
set -eu
export PATH="/usr/local/cargo/bin:$PATH"
export RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
# Admission strips .git; preserve the pinned baseline's build-version metadata.
export GIT_VERSION=2.56.0
export GIT_BUILT_FROM_COMMIT=92186d5b2a561afb5fe4b0dbce18a8906771faef
export GIT_DATE=2000-01-01
OUT=${1:-/app/build}
SRC=${OPTIMBENCH_SOURCE:-/app/git}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$SRC" "$STAGE/project"
cd "$STAGE/project"
# Keep debug/source paths independent of mktemp's random staging directory.
# Git defaults prefix to HOME; preserve the image's prefix in judge rebuilds.
make -j8 prefix=/root CFLAGS="-O3 -g -ffile-prefix-map=$STAGE/project=/build/git" NO_TCLTK=YesPlease
rm -rf "$OUT/project"
mv "$STAGE/project" "$OUT/project"
printf '#!/bin/sh\nexec python3 %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
