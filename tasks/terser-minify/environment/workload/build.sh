#!/bin/sh
# Build the pinned upstream distribution; the driver imports the copied production source.
set -eu
OUT=${1:-/app/build}
SRC=${TERSER_SOURCE:-/app/terser}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$SRC" "$STAGE/project"
rm -rf "$STAGE/project/.git" "$STAGE/project/node_modules"
ln -s /app/deps/node_modules "$STAGE/project/node_modules"
cd "$STAGE/project"
npm run build
rm -rf "$OUT/project"
mv "$STAGE/project" "$OUT/project"
printf '#!/bin/sh\nexec /usr/bin/python3 %s/runner.py --build "$(dirname "$0")" "$@"\n' "$HERE" > "$OUT/run"
chmod 755 "$OUT/run"
