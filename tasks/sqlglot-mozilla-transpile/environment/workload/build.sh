#!/bin/sh
# Pure Python: the offline source build copies the pinned package to its runtime.
set -eu
OUT=${1:-/app/build}
SRC=${SQLGLOT_SOURCE:-/app/sqlglot}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$SRC/sqlglot" "$STAGE/sqlglot"
PYTHONPATH="$STAGE" python -c 'import sqlglot; assert sqlglot.__version__ == "30.21.0"'
rm -rf "$OUT/runtime"
mkdir -p "$OUT/runtime"
mv "$STAGE/sqlglot" "$OUT/runtime/sqlglot"
printf '#!/bin/sh\nexec /usr/local/bin/python %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
