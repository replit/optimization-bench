#!/usr/bin/env bash
# Start containerd and dockerd in the foreground for agent runs.
# Uses a dedicated containerd root because a stale default root can hang on its bolt lock.
# Run it as a long-lived background process and never restart it mid-build or mid-trial.
set -euo pipefail

CTR_ROOT="${CTR_ROOT:-/var/lib/agent-containerd}"
CTR_STATE="${CTR_STATE:-/run/agent-containerd}"
SOCK="$CTR_STATE/containerd.sock"

if docker info >/dev/null 2>&1; then
  echo "docker already running"
  exec sleep infinity
fi

rm -rf "$CTR_STATE" /var/run/docker.sock /var/run/docker.pid
mkdir -p "$CTR_ROOT" "$CTR_STATE"
containerd --root "$CTR_ROOT" --state "$CTR_STATE" --address "$SOCK" >/tmp/agent-containerd.log 2>&1 &
for _ in $(seq 1 60); do [ -S "$SOCK" ] && break; sleep 1; done
[ -S "$SOCK" ] || { echo "containerd did not start; see /tmp/agent-containerd.log" >&2; exit 1; }
exec dockerd --containerd "$SOCK"
