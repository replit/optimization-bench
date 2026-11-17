#!/bin/sh
# Legacy Harbor verifier entry (score with judge.score instead); never include it in environment/.
set -eu
TASK_ROOT=${TASK_ROOT:-/opt/verifier/tasks/sqlglot-mozilla-transpile}
REPO=${VERIFIER_ROOT:-/opt/verifier}
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p /logs/verifier
# No implicit plumbing fallback: missing held-out data is an invalid scored run.
exec python3 -m judge.harbor --task "$TASK_ROOT" "$@"
