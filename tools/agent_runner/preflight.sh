#!/usr/bin/env bash
# Check that this machine can run an agent. Exits non-zero on the first failure.
set -euo pipefail

CLAUDE_BIN="${CLAUDE_BIN:-/root/agent-install/claude}"
CLAUDE_SHA256="${CLAUDE_SHA256:-b73676430af12f97316a798b8371f81d640f90a58bdcb8f3ce56a835bb47df56}"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

docker info >/dev/null 2>&1 || fail "docker daemon not reachable (start tools/agent_runner/start_docker.sh in the background)"
ok "docker $(docker version --format '{{.Server.Version}}')"
docker buildx version >/dev/null 2>&1 || fail "docker buildx missing"
ok "buildx"
docker compose version >/dev/null 2>&1 || fail "docker compose missing"
ok "compose"
n="$(docker run --rm --cpuset-cpus 0-7 -m 1g busybox nproc)"
[ "$n" = "8" ] || fail "cpuset 0-7 container saw $n CPUs"
ok "cpuset 0-7 container sees 8 CPUs"
echo "$CLAUDE_SHA256  $CLAUDE_BIN" | sha256sum -c --status || fail "claude binary hash mismatch"
ok "claude binary hash"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ok "ANTHROPIC_API_KEY set"
else
  echo "note: ANTHROPIC_API_KEY is not set in this shell; supply it to the agent at run time"
fi
echo "preflight passed"
