#!/bin/sh
set -eu
TASK_ROOT=${TASK_ROOT:-/opt/verifier/tasks/sqlite-distinct-plant-facets}
REPO=${VERIFIER_ROOT:-/opt/verifier}
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p /logs/verifier
exec python3 -m judge.harbor --task "$TASK_ROOT" "$@"
