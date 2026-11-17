#!/bin/sh
# Bootstrap only: avoid hydrating the repository's large LFS corpus on clone.
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: sh tools/clone_verifier_checkout.sh REPOSITORY_URL DESTINATION" >&2
  exit 2
fi
if ! command -v git >/dev/null || ! command -v git-lfs >/dev/null; then
  echo "Install first: sudo apt-get update && sudo apt-get install -y git git-lfs" >&2
  exit 1
fi
GIT_LFS_SKIP_SMUDGE=1 git clone -- "$1" "$2"
git -C "$2" lfs install --local --skip-smudge
echo "Checkout cloned without LFS hydration; recover only declared paths for the requested task."
