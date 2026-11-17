#!/bin/sh
# Build Black from the editable checkout (/app/black) as upstream ships it: a native mypyc wheel,
# offline, with the image's pinned build dependencies. OUT = runtime/ (the installed build) + run.
# Usage: sh /app/workload/build.sh [OUT=/app/build]
set -eu
OUT=${1:-/app/build}
SRC=${BLACK_SOURCE:-/app/black}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# hatch-mypyc 0.16 requires its build/ and tmp/ directories not to exist.
# Keep the parent and source paths stable for ccache, but recreate intermediates.
# This cache accelerates compilation only; it is never visible to workload cases.
CACHE=/app/.cache/black
mkdir -p "$CACHE"
exec 9>"$CACHE/build.lock"
flock 9
STAGE="$CACHE/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$CACHE/mypyc" "$CACHE/ccache" "$CACHE/mypy"
rm -rf "$CACHE/mypyc/build" "$CACHE/mypyc/tmp"
trap 'rm -rf "$STAGE"' EXIT
cp -a "$SRC" "$STAGE/project"
cd "$STAGE/project"
export HATCH_BUILD_HOOK_ENABLE_MYPYC=true SETUPTOOLS_SCM_PRETEND_VERSION=26.5.1 MYPYC_OPT_LEVEL=3
export HATCH_MYPYC_BUILD_DIR="$CACHE/mypyc" MYPY_CACHE_DIR="$CACHE/mypy"
export CC="python $HERE/compiler.py" CCACHE_DIR="$CACHE/ccache" CCACHE_BASEDIR="$CACHE"
export PIP_NO_INDEX=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
python -m build --wheel --no-isolation --outdir "$STAGE/wheels"
set -- "$STAGE"/wheels/*.whl
test "$#" = 1
python -m pip install --no-deps --no-index --target "$STAGE/runtime" "$1"
PYTHONPATH="$STAGE/runtime" python -c 'import black; assert black.COMPILED; assert black.__version__ == "26.5.1"'
rm -rf "$OUT/runtime"
mv "$STAGE/runtime" "$OUT/runtime"
printf '#!/bin/sh\nexec /usr/local/bin/python %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
