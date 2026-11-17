#!/bin/sh
# Instrumented pristine NumPy builds for feasibility (no task files changed).
# Usage: build-variant.sh <name> <meson setup args...>
set -eu
NAME=$1; shift
OUT=/out/$NAME
mkdir -p "$OUT/wheels"
ARGS=""
for a in "$@"; do ARGS="$ARGS -Csetup-args=$a"; done
cd /app/numpy
python3 -m pip wheel . --no-cache-dir --no-build-isolation --no-deps --no-index -w "$OUT/wheels" -Ccompile-args=-j8 $ARGS > "$OUT/build.log" 2>&1
python3 -m pip install --upgrade --no-index --no-deps --target "$OUT/site" "$OUT"/wheels/numpy-*.whl >> "$OUT/build.log" 2>&1
echo "$NAME ok"
