#!/bin/sh
# Compatibility verifier wrapper; scoring uses judge.score. Never include it in environment/.
set -eu
TASK_ROOT=${TASK_ROOT:-/opt/verifier/tasks/xz-lzma2-compress}
REPO=${VERIFIER_ROOT:-/opt/verifier}
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p /logs/verifier
# No implicit plumbing fallback: missing held-out data is an invalid scored run.
exec python3 -m judge.harbor --task "$TASK_ROOT" "$@"
