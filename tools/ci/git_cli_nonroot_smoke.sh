#!/bin/sh
# Build the git CLI stand-in image and run its healthy CLI cases as cli_faults does.
# Usage: tools/ci/git_cli_nonroot_smoke.sh <authenticated git upstream source dir>
set -eu
SOURCE=$(cd "$1" && pwd)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TASK="$ROOT/tasks/git-history-change-summary"
WORK=$(mktemp -d)
trap 'docker rm -f git-cli-standin-extract >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
mkdir "$WORK/context"
cp -a "$SOURCE" "$WORK/context/git"
cp -a "$TASK/environment/workload" "$WORK/context/workload"
rm -rf "$WORK/context/workload/inputs"
docker build -q -f "$ROOT/tools/ci/git-cli-standin.Dockerfile" --iidfile "$WORK/image" "$WORK/context" >/dev/null
IMAGE=$(cat "$WORK/image")
docker create --name git-cli-standin-extract "$IMAGE" >/dev/null
docker cp git-cli-standin-extract:/app/baseline-build "$WORK/build"
python3 -m tools.cli_nonroot_smoke --task "$TASK" --image "$IMAGE" --build "$WORK/build" --out "$WORK/out"
