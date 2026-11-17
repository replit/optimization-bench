#!/bin/sh
set -eu
export PATH="/usr/local/go/bin:$PATH"
OUT=${1:-/app/build}
test ! -e "$OUT" || { echo "Use a fresh build directory" >&2; exit 1; }
mkdir -p "$OUT"
cp -a "${OPTIMBENCH_SOURCE:-/app/goldmark}" "$OUT/project"
cd "$OUT/project"
go build -trimpath ./...
mkdir -p "$OUT/driver"
cp /app/workload/native/go.mod /app/workload/native/main.go "$OUT/driver/"
cd "$OUT/driver"
go mod edit "-replace=github.com/yuin/goldmark/v2=$OUT/project"
go build -trimpath -o "$OUT/goldmark-gfm-html" .
mkdir -p "$OUT/driver-parse"
cp /app/workload/native/go.mod "$OUT/driver-parse/" && cp /app/workload/native/parse/main.go "$OUT/driver-parse/"
cd "$OUT/driver-parse" && go mod edit "-replace=github.com/yuin/goldmark/v2=$OUT/project" && go build -trimpath -o "$OUT/goldmark-parse" .

cat > "$OUT/run" <<'SH'
#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 /app/workload/runner.py --task /app/workload/task.py --build "$HERE" --inputs /app/workload/inputs "$@"
SH
chmod +x "$OUT/run"
