#!/bin/sh
set -eu
export PATH="/usr/local/cargo/bin:$PATH" RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.93.1
OUT=${1:-/app/build}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT/project" "$OUT/driver" "$OUT/cargo-home"
cp -a "${OPTIMBENCH_SOURCE:-/app/serde-json}/." "$OUT/project/"
cp -a "$HERE/driver/." "$OUT/driver/"
cp -a /usr/local/cargo/registry /usr/local/cargo/git "$OUT/cargo-home/"
chmod -R u+w "$OUT/cargo-home"
export CARGO_HOME="$OUT/cargo-home"
cd "$OUT/driver"
CARGO_TARGET_DIR="$OUT/target" cargo build --release --offline --locked --config "patch.crates-io.serde_json.path=\"$OUT/project\""
cp "$OUT/target/release/optimbench-serde-index-smoke" "$OUT/prog"
# Second protected unit (geojson-typed): separate crate and target directory, so the
# registry driver above is built exactly as before.
cd "$OUT/driver/geojson"
CARGO_TARGET_DIR="$OUT/target-geojson" cargo build --release --offline --locked --config "patch.crates-io.serde_json.path=\"$OUT/project\""
cp "$OUT/target-geojson/release/optimbench-serde-geojson" "$OUT/prog-geojson"
printf '#!/bin/sh\nexec python3 /app/workload/runner.py --build "$(dirname "$0")" "$@"\n' > "$OUT/run"
chmod 755 "$OUT/run"
