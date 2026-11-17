#!/bin/sh
# Intended for Framework 2's pinned Go overlay. Source is never edited.
set -eu
: "${OPTIMBENCH_SOURCE:?Set pristine or candidate module root}"
: "${OPTIMBENCH_FUZZ_OUT:?Set a fresh output directory}"
test ! -e "$OPTIMBENCH_FUZZ_OUT"
mkdir -p "$OPTIMBENCH_FUZZ_OUT/module"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cp "$HERE/native/go.mod" "$HERE/native/main.go" "$OPTIMBENCH_FUZZ_OUT/module/"
cd "$OPTIMBENCH_FUZZ_OUT/module"
go mod edit "-replace=github.com/yuin/goldmark/v2=$OPTIMBENCH_SOURCE"
if test "${OPTIMBENCH_GO_RACE:-0}" = 1; then
 CGO_ENABLED=1 go build -mod=mod -race -trimpath -o "$OPTIMBENCH_FUZZ_OUT/worker" .
else
 go build -mod=mod -trimpath -o "$OPTIMBENCH_FUZZ_OUT/worker" .
fi
