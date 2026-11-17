#!/bin/sh
# Offline source wheel build; every invocation replaces the previous runtime.
set -eu
OUT=${1:-/app/build}
SRC=${SQLFLUFF_SOURCE:-/app/sqlfluff}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$SRC" "$STAGE/project"
cd "$STAGE/project"
export PIP_NO_INDEX=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
python -m build --wheel --no-isolation --outdir "$STAGE/wheels"
set -- "$STAGE"/wheels/*.whl
test "$#" = 1
python -m pip install --no-deps --no-index --target "$STAGE/runtime" "$1"
PYTHONPATH="$STAGE/runtime" python -c 'import sqlfluff; assert sqlfluff.__version__ == "4.4.0"'
rm -rf "$OUT/runtime"
mv "$STAGE/runtime" "$OUT/runtime"
printf '#!/bin/sh\nexec /usr/local/bin/python %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
