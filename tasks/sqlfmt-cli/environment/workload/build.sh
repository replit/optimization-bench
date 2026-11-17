#!/bin/sh
# Build both entry points from exactly the submitted tree, offline and CGO off.
# Usage: sh /app/workload/build.sh [OUT=/app/build]
set -eu
export PATH="/usr/local/go/bin:$PATH" CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOPATH=/go GOMODCACHE=/go/pkg/mod
OUT=${1:-/app/build}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
SOURCE=$(cd "${OPTIMBENCH_SOURCE:-/app/sqlfmt}" && pwd)
case "$SOURCE/" in "$OUT/"*) echo "Source must be outside build output" >&2; exit 2 ;; esac
# Keep the compiler cache, never an overlaid source tree or stale driver.
rm -rf "$OUT/project" "$OUT/native-driver"
mkdir -p "$OUT/project"
cp -a "$SOURCE/." "$OUT/project/"
cd "$OUT/project"
GOCACHE="$OUT/go-cache" go build -trimpath -o "$OUT/sqlfmt" ./cmd/sqlfmt
# Both entry points link the same copied, rebuilt candidate source. Protected
# caller source stays outside the candidate checkout and module policy.
mkdir -p "$OUT/native-driver"
cp "$HERE/native/main.go" "$OUT/native-driver/main.go"
printf 'module optimbench-sqlfmt-native\n\ngo 1.26.5\n\nrequire github.com/dimitri/sqlfmt v0.0.0\n\nreplace github.com/dimitri/sqlfmt => ../project\n' > "$OUT/native-driver/go.mod"
cd "$OUT/native-driver"
GOCACHE="$OUT/go-cache" go build -trimpath -o "$OUT/sqlfmt-native" .
python3 "$HERE/native/build_receipt.py" "$OUT" "$HERE"
printf '#!/bin/sh\nexec python3 /app/workload/runner.py --build "$(dirname "$0")" "$@"\n' > "$OUT/run"
chmod 755 "$OUT/run"
