#!/bin/sh
set -eu
# Jen projekt jrc-p1b33. Prohlížeč a Node sdílejí 3 GiB cgroup.
[ "${JRC_PROJECT:-}" = jrc-p1b33 ] || exit 2
timeout 600s docker run --rm --name jrc-p1b33-browser --pull never --network host \
  --memory 3g --memory-swap 3g --cpus 2 --pids-limit 256 \
  --user "$(id -u):$(id -g)" --volume "$(pwd):/workspace" \
  jrc-dev:jrc-p1b33 timeout -k 10s 540s node --max-old-space-size=2048 /workspace/scripts/P1-B33/browser.cjs
