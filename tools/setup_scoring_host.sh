#!/bin/sh
# Run before reserving a quiet host, never beside a timed session.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

VENV=${JUDGE_VENV:-/opt/judge}
PYTHON=${JUDGE_PYTHON:-python3}
command -v "$PYTHON" >/dev/null || {
  echo "Install first: sudo apt-get update && sudo apt-get install -y python3 python3-venv" >&2
  exit 1
}
"$PYTHON" -m tools.scoring_host runtime-check
"$PYTHON" -m venv "$VENV" || {
  echo "Install python3-venv and choose a writable JUDGE_VENV (default /opt/judge)." >&2
  echo "sudo apt-get update && sudo apt-get install -y python3-venv" >&2
  exit 1
}
"$VENV/bin/pip" install --require-hashes --only-binary=:all: \
  -r judge/requirements-interface.lock -r judge/requirements-host-registry.lock
{ command -v git >/dev/null && command -v git-lfs >/dev/null; } || {
  echo "Install first: sudo apt-get update && sudo apt-get install -y git git-lfs" >&2
  exit 1
}
# Local to this verifier checkout, never global smudge/pull or private acquisition.
# Do not --force over an operator's existing hook.
git lfs install --local --skip-smudge
"$VENV/bin/python" -m tools.scoring_host check "$@"
echo "Setup sanity passed. Run the judge with $VENV/bin/python."
echo "This is not a task/image execution check or proof the host is quiet."
