#!/usr/bin/env bash
# Run on an isolated scoring host with reserved physical CPUs 0-7.
# Pass an admitted real task/build/image and a fresh output directory:
# bash tools/prove_cpu_correctness.sh --task TASK --baseline BUILD --candidate BUILD \
#   --image sha256:IMAGE_ID --out FRESH --public-inputs INPUTS --projects PROJECTS
# This does not build candidates, acquire inputs, run timing, or manufacture cases.
set -euo pipefail
cd "$(dirname "$0")/.."
docker info >/dev/null
exec python3 -m judge.cpu_correctness "$@"
