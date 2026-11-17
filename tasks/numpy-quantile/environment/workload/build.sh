#!/bin/sh
set -eu
OUT=${1:-/app/build}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
SOURCE=${NUMPY_SOURCE:-/app/numpy}
WHEELS="$OUT/wheels"
mkdir -p "$WHEELS"
python3 -m pip wheel "$SOURCE" --no-cache-dir --no-build-isolation --no-deps --no-index -w "$WHEELS" -Ccompile-args=-j8 -Csetup-args=-Dbuildtype=release
python3 -m pip install --upgrade --no-index --no-deps --target "$OUT/site" "$WHEELS"/numpy-*.whl


cat > "$OUT/run" <<EOF
#!/bin/sh
set -eu
exec python3 /app/workload/runner.py --build "$OUT" "\$@"
EOF
chmod 755 "$OUT/run"
