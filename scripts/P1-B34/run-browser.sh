#!/bin/sh
set -eu
case "${1:-}" in before|after) ;; *) exit 2;; esac
# Jen vlastní projekt. Prohlížeč i Node sdílejí cgroup 3 GiB.
[ "${JRC_PROJECT:-}" = jrc-p1b34 ] || exit 2
timeout 600s docker run --rm --name jrc-p1b34-browser --pull never --network host \
  --memory 3g --memory-swap 3g --cpus 2 --pids-limit 256 \
  --user "$(id -u):$(id -g)" --volume "$(pwd):/workspace" \
  jrc-dev:jrc-p1b34 timeout -k 10s 540s node --max-old-space-size=2048 /workspace/scripts/P1-B34/browser.cjs "$1"
