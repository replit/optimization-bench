#!/usr/bin/env bash
# Prepare a machine to run OptimizationBench agents (not a scoring host): Docker plugins
# and the pinned Claude Code binary. Idempotent. Run as root.
set -euo pipefail

CLAUDE_VERSION="${CLAUDE_VERSION:-2.1.310}"
CLAUDE_SHA256="${CLAUDE_SHA256:-b73676430af12f97316a798b8371f81d640f90a58bdcb8f3ce56a835bb47df56}"
CLAUDE_BIN="${CLAUDE_BIN:-/root/agent-install/claude}"

export DEBIAN_FRONTEND=noninteractive
# Install only what is missing, judged by working commands rather than package
# names, so an existing Docker engine (e.g. Docker CE) is never replaced.
missing=""
command -v dockerd >/dev/null 2>&1 || missing="$missing docker.io"
command -v containerd >/dev/null 2>&1 || missing="$missing containerd"
docker buildx version >/dev/null 2>&1 || missing="$missing docker-buildx"
docker compose version >/dev/null 2>&1 || missing="$missing docker-compose-v2"
command -v git >/dev/null 2>&1 || missing="$missing git"
command -v git-lfs >/dev/null 2>&1 || missing="$missing git-lfs"
python3 -c 'import ensurepip, venv' >/dev/null 2>&1 || missing="$missing python3-venv"
command -v curl >/dev/null 2>&1 || missing="$missing curl ca-certificates"
if [ -n "$missing" ]; then
  apt-get update -q
  apt-get install -y -q --no-remove $missing
fi

git lfs install --system --skip-smudge >/dev/null

have_sha() { [ -f "$CLAUDE_BIN" ] && echo "$CLAUDE_SHA256  $CLAUDE_BIN" | sha256sum -c --status; }
if ! have_sha; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL https://claude.ai/install.sh -o "$tmp/install.sh"
  # The installer refuses to run as root unless told to; this script installs a
  # pinned, hash-checked binary for root on purpose.
  HOME="$tmp/home" CLAUDE_INSTALL_ALLOW_SUDO=1 bash "$tmp/install.sh" "$CLAUDE_VERSION" >/dev/null
  mkdir -p "$(dirname "$CLAUDE_BIN")"
  install -m 0755 "$(readlink -f "$tmp/home/.local/bin/claude")" "$CLAUDE_BIN"
  have_sha || { echo "Claude Code binary hash mismatch" >&2; exit 1; }
fi

echo "agent runner ready: claude $CLAUDE_VERSION ($CLAUDE_SHA256)"
