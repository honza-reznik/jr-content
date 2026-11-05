#!/bin/sh
# Omezení platí i pro procesy Chromia; pouze existující místní obraz.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b19 ] || exit 2
mkdir -p ops/test-output/P1-B19 ops/screenshoty/P1-B19
timeout 600 docker run --rm --name jrc-p1b19-browser --init --pull never --network host \
  --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --volume "$PWD:/workspace" --workdir /workspace \
  jrc-dev:jrc-p1b19 timeout 540 node --max-old-space-size=2048 scripts/P1-B19/browser.cjs
