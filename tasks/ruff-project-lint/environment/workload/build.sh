#!/bin/sh
# Offline release build. Reuse the target directory for developer rebuilds.
set -eu
OUT=${1:-/app/build}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "${RUFF_SOURCE:-/app/ruff}"
export PATH=/toolchain/bin:/usr/local/bin:/usr/bin:/bin
export CARGO_HOME="$OUT/cargo-home" CARGO_TARGET_DIR="$OUT/target"
export CI=1 INSTA_UPDATE=no RAYON_NUM_THREADS=8
cargo --config 'source.crates-io.replace-with="vendored-sources"' \
  --config 'source.vendored-sources.directory="/assets/vendor"' \
  build --locked --offline --release -p ruff --bin ruff -j 8
cp "$OUT/target/release/ruff" "$OUT/ruff"
printf '#!/bin/sh\nexec /usr/bin/python3 %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
