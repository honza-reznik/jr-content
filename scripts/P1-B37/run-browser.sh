#!/bin/sh
set -eu
case "${1:-}" in before|after) ;; *) exit 2;; esac
# Jen vlastní projekt. Prohlížeč i Node sdílejí cgroup 3 GiB. Testovací videa jen lokální, jen pro čtení.
[ "${JRC_PROJECT:-}" = jrc-p1b37 ] || exit 2
timeout 600s docker run --rm --name jrc-p1b37-browser --pull never --network host \
  --memory 3g --memory-swap 3g --cpus 2 --pids-limit 256 \
  --user "$(id -u):$(id -g)" --volume "$(pwd):/workspace" \
  --volume /home/honza/jr-content-wt/TEST/ops/screenshoty/media:/media:ro \
  jrc-dev:jrc-p1b37 timeout -k 10s 540s node --max-old-space-size=2048 /workspace/scripts/P1-B37/browser.cjs "$1"
